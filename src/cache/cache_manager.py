"""
多级缓存管理器 — L1 本地 LRU + L2 Redis + 回源

缓存策略：
    L1 (本地 LRU)  : 进程内缓存，命中极快，存放热点数据
    L2 (Redis)     : 分布式缓存，跨进程共享，适合多实例部署；连接失败时静默降级为仅本地缓存

TTL 梯度：
    CACHE_TTL_SHORT  (5分钟)  — 评论、弹幕
    CACHE_TTL_MEDIUM (30分钟) — 视频元数据、字幕、搜索
    CACHE_TTL_LONG   (24小时) — 用户信息
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import time
from typing import Any, Callable

from loguru import logger

from src.config import settings


class LRUCache:
    """
    本地 LRU 缓存（利用 Python 3.7+ dict 保序特性）。

    每次访问时把 key 移到末尾（标记为最近使用），
    淘汰时删除最前面的 key（最久未使用）。
    通过 asyncio.Lock 保证协程安全，适用于单进程 asyncio 环境。
    """

    def __init__(self, max_size: int = 2000):
        self.max_size: int = max_size
        self._store: dict[str, tuple[Any, float]] = {}  # key → (value, expire_at)
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.monotonic() > expiry:
                del self._store[key]
                return None
            # 移到末尾 = 标记为最近使用
            del self._store[key]
            self._store[key] = (value, expiry)
            return value

    async def set(self, key: str, value: Any, ttl: int = 300):
        async with self._lock:
            if key in self._store:
                del self._store[key]
            elif len(self._store) >= self.max_size:
                oldest = next(iter(self._store))
                del self._store[oldest]
            self._store[key] = (value, time.monotonic() + ttl)

    async def delete(self, key: str):
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self):
        async with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


class RedisCache:
    """Redis 二级缓存封装，连接失败时静默降级为仅本地缓存"""

    def __init__(self):
        self._client: Any | None = None
        self._available: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._available

    async def start(self):
        """主动建立连接（幂等）；未启用或连接失败时静默降级"""
        if not settings.REDIS_ENABLED:
            return
        await self._ensure_client()

    async def stop(self):
        if self._client is not None:
            try:
                await self._client.aclose()
            except AttributeError:
                await self._client.close()
            except Exception:
                pass
            self._client = None
            self._available = False

    async def _ensure_client(self):
        if not settings.REDIS_ENABLED or self._available:
            return
        if self._client is not None:
            return
        async with self._lock:
            if self._client is not None:
                return
            try:
                import redis.asyncio as aioredis

                self._client = aioredis.from_url(
                    settings.REDIS_URL,
                    socket_connect_timeout=3,
                    socket_timeout=3,
                    protocol=2,
                )
                await self._client.ping()
                self._available = True
                logger.info("Redis 二级缓存已连接")
            except Exception as e:
                logger.warning(f"Redis 连接失败，仅使用本地缓存: {e}")
                self._available = False
                self._client = None

    @staticmethod
    def _prefix(key: str) -> str:
        return f"bili:{key}"

    async def get(self, key: str) -> Any | None:
        if not settings.REDIS_ENABLED:
            return None
        await self._ensure_client()
        client = self._client
        if client is None:
            return None
        try:
            raw = await client.get(self._prefix(key))
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    async def set(self, key: str, value: Any, ttl: int = 300):
        if not settings.REDIS_ENABLED:
            return
        await self._ensure_client()
        client = self._client
        if client is None:
            return
        try:
            if hasattr(value, "model_dump"):
                payload = value.model_dump()
            elif hasattr(value, "dict"):
                payload = value.dict()
            else:
                payload = value
            await client.setex(
                self._prefix(key),
                ttl,
                json.dumps(payload, ensure_ascii=False, default=str),
            )
        except Exception as e:
            logger.warning(f"Redis 写入失败: {e}")

    async def delete(self, key: str):
        if not settings.REDIS_ENABLED:
            return
        await self._ensure_client()
        client = self._client
        if client is None:
            return
        try:
            await client.delete(self._prefix(key))
        except Exception:
            pass


class CacheManager:
    """多级缓存协调器 — L1 → L2 → 回源"""

    def __init__(self):
        self.l1: LRUCache = LRUCache(max_size=2000)
        self.l2: RedisCache = RedisCache()

    async def start(self):
        await self.l2.start()

    async def stop(self):
        await self.l2.stop()

    async def get(self, key: str) -> Any | None:
        """多级读取：L1 → L2 → None"""
        value = await self.l1.get(key)
        if value is not None:
            return value
        value = await self.l2.get(key)
        if value is not None:
            await self.l1.set(key, value, ttl=settings.CACHE_TTL_MEDIUM)  # 回写 L1
            return value
        return None

    async def set(self, key: str, value: Any, ttl: int | None = None):
        """写入多级缓存"""
        if ttl is None:
            ttl = settings.CACHE_TTL_MEDIUM
        await self.l1.set(key, value, ttl)
        await self.l2.set(key, value, ttl)

    async def delete(self, key: str):
        await self.l1.delete(key)
        await self.l2.delete(key)

    async def invalidate(self, key: str):
        """兼容别名：与 delete 语义一致"""
        await self.delete(key)


# ============================================================
# 全局单例
# ============================================================

_cache_manager: CacheManager | None = None


def get_cache_manager() -> CacheManager:
    """获取全局缓存管理器单例"""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager


def get_cache() -> CacheManager:
    """别名，兼容旧调用方"""
    return get_cache_manager()


# ============================================================
# @cached 装饰器
# ============================================================

def cached(key_prefix: str, ttl: int | None = None, ttl_short: bool = False):
    """
    缓存装饰器，自动缓存异步函数的返回值。

    使用示例：
        @cached("video", ttl=1800)
        async def get_video_meta(bvid: str) -> dict: ...

    注意：返回值为 None 时不写入缓存（避免对空结果做无意义的缓存与回源）。
    """
    def decorator(func: Callable[..., Any]):
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            cache = get_cache_manager()
            key_parts = [key_prefix]
            if args:
                key_parts.append(str(args))
            if kwargs:
                key_parts.append(str(sorted(kwargs.items())))
            key_raw = ":".join(key_parts)
            cache_key = f"{key_prefix}:{hashlib.md5(key_raw.encode()).hexdigest()[:16]}"

            cached_value = await cache.get(cache_key)
            if cached_value is not None:
                return cached_value

            result = await func(*args, **kwargs)
            if result is None:
                return result
            _ttl = ttl if ttl is not None else (
                settings.CACHE_TTL_SHORT if ttl_short else settings.CACHE_TTL_MEDIUM
            )
            await cache.set(cache_key, result, ttl=_ttl)
            return result
        return wrapper
    return decorator
