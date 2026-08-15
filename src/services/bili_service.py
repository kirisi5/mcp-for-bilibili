"""
业务服务层 — 编排多个爬虫方法，提供业务语义的操作。

设计模式：Facade（门面模式）— 简化下层爬虫的复杂调用
核心功能：BiliVideoService（视频一站式查询）、BiliSearchService（搜索）、BiliUserService（用户）
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TypeVar

from src.crawler.bili_client import get_bili_client
from src.cache.cache_manager import get_cache
from src.models.schemas import (
    VideoMeta, VideoPage, SubtitleResult, Comment, UserInfo, MyInfo,
    SearchResult, VideoFullView, ChapterItem, InteractionInfo,
    PageInfo, Danmaku,
)

T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    简单熔断器。
    连续 failure_threshold 次失败后切换到 OPEN，
    recovery_timeout 秒后进入 HALF_OPEN 试探。
    """

    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: float = 10.0):
        self.name = name; self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count = 0; self._last_failure_time = 0.0
        self._state = CircuitState.CLOSED; self._lock = asyncio.Lock()

    async def call(self, coro):
        """通过熔断器执行协程"""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                else:
                    raise RuntimeError(f"熔断器 [{self.name}] 已打开，请稍后重试")
        try:
            result = await coro
            async with self._lock:
                if self._state == CircuitState.HALF_OPEN:
                    self._state = CircuitState.CLOSED
                self._failure_count = 0
            return result
        except Exception as e:
            async with self._lock:
                self._failure_count += 1
                self._last_failure_time = time.time()
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
            raise e


class BiliVideoService:
    """视频业务服务 — 缓存前置 + 降级容错"""

    def __init__(self):
        self.client = get_bili_client()
        self.cache = get_cache()

    async def get_video_meta(self, bvid: str) -> VideoMeta:
        cache_key = f"video:{bvid}"
        cached_value = await self.cache.get(cache_key)
        if cached_value:
            return VideoMeta(**cached_value) if isinstance(cached_value, dict) else cached_value
        result = await self.client.get_video_meta(bvid)
        await self.cache.set(cache_key, result)
        return result

    async def get_video_pages(self, bvid: str) -> list[VideoPage]:
        cache_key = f"video:pages:{bvid}"
        cached_value = await self.cache.get(cache_key)
        if cached_value:
            return [VideoPage(**p) if isinstance(p, dict) else p for p in cached_value]
        result = await self.client.get_video_pages(bvid)
        await self.cache.set(cache_key, [p.model_dump() for p in result])
        return result

    async def get_video_subtitle(self, bvid: str, cid: int) -> SubtitleResult | None:
        cache_key = f"subtitle:{bvid}:{cid}"
        cached_value = await self.cache.get(cache_key)
        if cached_value is not None:
            if cached_value == "__NONE__":
                return None
            return SubtitleResult(**cached_value) if isinstance(cached_value, dict) else cached_value
        result = await self.client.get_video_subtitle(bvid, cid)
        if not result:
            await self.cache.set(cache_key, "__NONE__", ttl=3600)  # 无字幕缓存 1 小时
            return None
        subtitle = SubtitleResult(subtitles=result, source="cc_subtitle")
        await self.cache.set(cache_key, subtitle.model_dump())
        return subtitle

    async def get_video_chapters(self, bvid: str, cid: int) -> list[ChapterItem]:
        return await self.client.get_video_chapters(bvid, cid)

    async def get_comments(self, oid: int, page: int = 1, sort: int = 1) -> tuple[list[Comment], PageInfo]:
        return await self.client.get_comments(oid, page=page, sort=sort)

    async def get_danmaku(self, cid: int, segment_index: int = 1) -> list[Danmaku]:
        return await self.client.get_danmaku(cid, segment_index)

    async def get_video_full_view(self, bvid: str) -> VideoFullView:
        """
        获取视频全貌 — 并行调用多个 API 聚合所有信息。

        关键设计：
        - 先获取元数据（后续接口依赖 aid/cid）
        - 然后并行获取字幕、章节、热评、互动（含相关推荐）
        - 任何子接口失败不影响整体返回（降级策略）
        """
        meta = await self.get_video_meta(bvid)

        # 并行获取所有子信息（asyncio.gather 实现真实并发）
        subtitle_task = self._safe_fetch(lambda: self.get_video_subtitle(bvid, meta.cid))
        chapters_task = self._safe_fetch(lambda: self.get_video_chapters(bvid, meta.cid), default=[])
        comments_task = self._safe_fetch(
            lambda: self.get_comments(meta.aid, page=1, sort=1),
            default=([], PageInfo()),
        )
        interaction_task = self._safe_fetch(lambda: self.client.get_interaction(bvid, meta.aid))

        subtitle, chapters, comments, interaction = await asyncio.gather(
            subtitle_task, chapters_task, comments_task, interaction_task,
            return_exceptions=True,
        )

        # _safe_fetch 已捕获异常返回默认值，这里再做类型兜底过滤
        subtitle = subtitle if isinstance(subtitle, SubtitleResult) else None
        chapters = chapters if isinstance(chapters, list) else []
        hot_comments = comments[0] if isinstance(comments, tuple) and comments else []
        interaction = interaction if isinstance(interaction, InteractionInfo) else None

        return VideoFullView(
            meta=meta,
            subtitle=subtitle,
            chapters=chapters,
            hot_comments=hot_comments,
            interaction=interaction,
            related_videos=interaction.related_videos if interaction else [],
        )

    @staticmethod
    async def _safe_fetch(fn: Callable[[], Awaitable[T]], default: T | None = None) -> T | None:
        """安全执行：捕获异常返回默认值，不中断主流程"""
        try:
            return await fn()
        except Exception:
            return default


class BiliSearchService:
    def __init__(self):
        self.client = get_bili_client(); self.cache = get_cache()

    async def search(self, keyword: str, page: int = 1, ps: int = 20) -> SearchResult:
        cache_key = f"search:{keyword}:{page}"
        cached_value = await self.cache.get(cache_key)
        if cached_value:
            return SearchResult(**cached_value) if isinstance(cached_value, dict) else cached_value
        result = await self.client.search(keyword, page, ps)
        await self.cache.set(cache_key, result.model_dump())
        return result


class BiliUserService:
    def __init__(self):
        self.client = get_bili_client(); self.cache = get_cache()

    async def get_user_info(self, mid: int) -> UserInfo:
        cache_key = f"user:{mid}"
        cached_value = await self.cache.get(cache_key)
        if cached_value:
            return UserInfo(**cached_value) if isinstance(cached_value, dict) else cached_value
        result = await self.client.get_user_info(mid)
        await self.cache.set(cache_key, result.model_dump())
        return result

    async def get_my_info(self) -> MyInfo:
        return await self.client.get_my_info()