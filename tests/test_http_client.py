"""
HTTP 客户端与限流器单元测试

运行方式：
    pytest tests/test_http_client.py -v
    pytest tests/ -v --cov=src --cov-report=term-missing
"""

import asyncio
import time
import pytest
from src.crawler.http_client import RateLimitBucket


class TestRateLimitBucket:
    """令牌桶限流器测试"""

    async def test_basic_acquire(self):
        """测试基本令牌获取：初始满桶应该瞬间完成"""
        bucket = RateLimitBucket(name="test", max_rate=10.0, bucket_max=10)
        start = time.monotonic()
        for _ in range(10):
            await bucket.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"初始满桶不应阻塞: {elapsed:.2f}s"

    async def test_rate_limiting(self):
        """测试限流效果：超配额请求应该被延迟"""
        bucket = RateLimitBucket(name="test", max_rate=5.0, bucket_max=5)
        # 初始满桶的 5 个瞬间获取
        start = time.monotonic()
        for _ in range(5):
            await bucket.acquire()
        instant_elapsed = time.monotonic() - start
        assert instant_elapsed < 0.1

        # 第 6 个需要等待令牌补充（速率 5/s 意味着等 ~0.2s）
        await bucket.acquire()
        total_elapsed = time.monotonic() - start
        assert total_elapsed > 0.1, f"第6次应受限: {total_elapsed:.3f}s"

    async def test_rate_reduction_on_412(self):
        """测试 412 响应触发降速"""
        bucket = RateLimitBucket(name="test", max_rate=10.0)
        original_rate = bucket.rate
        await bucket.report_ratelimited()
        assert bucket.rate <= original_rate / 2, f"速率应减半: {bucket.rate} vs {original_rate/2}"

    async def test_rate_recovery(self):
        """测试连续成功后试探提速"""
        bucket = RateLimitBucket(name="test", max_rate=10.0)
        bucket.rate = 5.0  # 模拟降速后的状态
        for _ in range(10):
            await bucket.report_success()
        assert bucket.rate > 5.0, f"应提速: {bucket.rate} > 5.0"
        assert bucket.rate <= 10.0, f"不超上限: {bucket.rate} <= 10.0"


# ── 以下为待实现的练习用例 ──

class TestHTTPClient:
    """HTTP 客户端集成测试（需要 mock B站 API）"""

    async def test_signed_get_injects_wbi_params(self):
        """练习：测试签名后的请求包含 w_rid 和 wts 参数"""
        pass

    async def test_retry_on_5xx(self):
        """练习：测试 5xx 触发自动重试"""
        pass

    async def test_cache_hit_avoids_http(self):
        """练习：测试缓存命中时跳过 HTTP 请求"""
        pass