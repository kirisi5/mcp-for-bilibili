"""
HTTP 客户端 — 自适应令牌桶限流 + 连接池复用 + 自动重试

核心设计：
1. RateLimitBucket：自适应令牌桶，支持动态降速和试探性提速
2. HTTPClientManager：httpx.AsyncClient 单例，统一管理连接池/Cookie/重试/trace_id
3. 每个场景独立限流桶，避免「搜索被限流导致视频信息也拉不到」

使用示例：
    async with http_session("video") as session:
        resp = await session.get("https://api.bilibili.com/x/web-interface/view", params={"bvid": "BV1xx"})
"""

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from src.config import settings


class RateLimitBucket:
    """
    自适应令牌桶限流器。

    自适应机制：
    - 收到 412 响应（B站限流信号）→ 速率减半（降速保护）
    - 连续成功 10 次 → 速率 +10%（试探性提速，不超过初始值）
    """

    def __init__(self, name: str, max_rate: float, bucket_max: int = 10):
        self.name = name
        self.max_rate = max_rate       # 用户配置的最大速率
        self.rate = max_rate           # 当前实际速率（动态调整）
        self.capacity = bucket_max
        self._tokens = max_rate        # 当前令牌数（初始满桶）
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._success_streak = 0       # 连续成功计数

    async def _refill(self):
        """补充令牌（基于时间差计算）"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        # 关键公式：新增令牌 = 经过时间 × 补充速率
        new_tokens = elapsed * self.rate
        self._tokens = min(self._tokens + new_tokens, self.capacity)
        self._last_refill = now

    async def acquire(self):
        """获取一个令牌。令牌不足则等待直到可用。"""
        async with self._lock:
            await self._refill()
            if self._tokens >= 1:
                self._tokens -= 1
                return
            wait_time = (1 - self._tokens) / self.rate
        # 在锁外等待，不影响其他协程
        await asyncio.sleep(wait_time)
        async with self._lock:
            await self._refill()
            self._tokens -= 1

    async def report_success(self):
        """上报成功，用于自适应提速"""
        async with self._lock:
            self._success_streak += 1
            if self._success_streak >= 10 and self.rate < self.max_rate:
                self.rate = min(self.rate * 1.1, self.max_rate)
                self._success_streak = 0

    async def report_ratelimited(self):
        """上报被限流（412），触发降速"""
        async with self._lock:
            self._success_streak = 0
            self.rate = max(self.rate * 0.5, 0.1)


# 按场景映射限流值
_SCENE_RATE_MAP = {
    "default": lambda: settings.RATE_LIMIT_DEFAULT,
    "video":   lambda: settings.RATE_LIMIT_VIDEO,
    "search":  lambda: settings.RATE_LIMIT_SEARCH,
    "comment": lambda: settings.RATE_LIMIT_COMMENT,
    "danmaku": lambda: settings.RATE_LIMIT_DANMAKU,
}


class SceneRateLimiter:
    """管理多个场景的独立令牌桶"""

    def __init__(self):
        self._buckets: dict[str, RateLimitBucket] = {}

    def get_bucket(self, scene: str) -> RateLimitBucket:
        if scene not in self._buckets:
            rate = _SCENE_RATE_MAP.get(scene, _SCENE_RATE_MAP["default"])()
            self._buckets[scene] = RateLimitBucket(name=scene, max_rate=rate)
        return self._buckets[scene]


_scene_limiter = SceneRateLimiter()


class HTTPClientManager:
    """
    企业级 HTTP 客户端管理器。

    职责：
    1. httpx.AsyncClient 单例管理 — 复用连接池，避免反复握手
    2. Cookie 注入 — 自动附加 B站认证
    3. trace_id 生成 — 用于链路追踪和日志串联
    4. 自动重试 — 指数退避，处理 429/412/5xx
    5. 场景限流 — 请求前后自动获取令牌和上报结果
    """

    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._lock:
                if self._client is None:  # 双重检查
                    self._client = httpx.AsyncClient(
                        timeout=httpx.Timeout(settings.HTTP_TIMEOUT),
                        limits=httpx.Limits(
                            max_connections=settings.HTTP_MAX_CONNECTIONS,
                            max_keepalive_connections=20,
                        ),
                        cookies=settings.bili_cookies,
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/120.0.0.0 Safari/537.36"
                            ),
                            "Referer": "https://www.bilibili.com/",
                        },
                    )
        return self._client

    async def request(self, method: str, url: str, scene: str = "default", **kwargs) -> httpx.Response:
        """发起 HTTP 请求（带限流 + 重试 + trace_id）"""
        client = await self._get_client()
        bucket = _scene_limiter.get_bucket(scene)

        # 注入 trace_id 用于链路追踪
        trace_id = uuid.uuid4().hex[:16]
        kwargs.setdefault("headers", {})
        kwargs["headers"]["X-Trace-Id"] = trace_id

        last_exception = None

        for attempt in range(settings.HTTP_MAX_RETRIES + 1):
            # 步骤1：限流检查
            await bucket.acquire()

            try:
                response = await client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_exception = e
                if attempt < settings.HTTP_MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)  # 指数退避: 1s→2s→4s
                    continue
                raise

            # 步骤2：根据状态码处理
            if response.status_code == 412:
                await bucket.report_ratelimited()  # B站限流 → 降速
                await asyncio.sleep(2 ** attempt)
                continue
            if response.status_code == 429:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            if response.status_code >= 500:
                if attempt < settings.HTTP_MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                    continue
                response.raise_for_status()

            # 步骤3：成功
            if response.status_code < 400:
                await bucket.report_success()
            else:
                response.raise_for_status()
            return response

        if last_exception:
            raise last_exception
        raise RuntimeError(f"Max retries exceeded for {url}")

    async def get(self, url: str, scene: str = "default", **kwargs) -> httpx.Response:
        return await self.request("GET", url, scene=scene, **kwargs)

    async def post(self, url: str, scene: str = "default", **kwargs) -> httpx.Response:
        return await self.request("POST", url, scene=scene, **kwargs)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


_http_manager = HTTPClientManager()


def get_http_client() -> HTTPClientManager:
    return _http_manager


@asynccontextmanager
async def http_session(scene: str = "default") -> AsyncGenerator[HTTPClientManager, None]:
    """异步上下文管理器，提供场景限流的 HTTP 会话"""
    yield _http_manager