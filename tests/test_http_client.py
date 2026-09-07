"""HTTP 客户端、签名、重试和缓存测试。"""

import time
from unittest.mock import AsyncMock

import httpx
import pytest

from src.crawler.bili_client import BiliClient, _build_danmaku_message_class
from src.crawler.http_client import HTTPClientManager, RateLimitBucket
from src.services.bili_service import BiliVideoService


class TestRateLimitBucket:
    async def test_basic_acquire(self):
        bucket = RateLimitBucket(name="test", max_rate=10.0, bucket_max=10)
        start = time.monotonic()
        for _ in range(10):
            await bucket.acquire()
        assert time.monotonic() - start < 1.0

    async def test_rate_limiting(self):
        bucket = RateLimitBucket(name="test", max_rate=5.0, bucket_max=5)
        start = time.monotonic()
        for _ in range(5):
            await bucket.acquire()
        await bucket.acquire()
        assert time.monotonic() - start > 0.1

    async def test_rate_reduction_on_412(self):
        bucket = RateLimitBucket(name="test", max_rate=10.0)
        await bucket.report_ratelimited()
        assert bucket.rate == 5.0

    async def test_rate_recovery(self):
        bucket = RateLimitBucket(name="test", max_rate=10.0)
        bucket.rate = 5.0
        for _ in range(10):
            await bucket.report_success()
        assert 5.0 < bucket.rate <= 10.0


class TestHTTPClient:
    async def test_signed_get_injects_wbi_params(self, monkeypatch):
        captured = {}

        class Signer:
            async def sign_params(self, params):
                return {**params, "wts": "123", "w_rid": "abc"}

        class Client:
            async def get(self, url, **kwargs):
                captured.update(kwargs)
                return "response"

        monkeypatch.setattr("src.crawler.bili_client.get_wbi_signer", lambda: Signer())
        monkeypatch.setattr("src.crawler.bili_client.get_http_client", lambda: Client())
        response = await BiliClient()._signed_get("https://example.test", {"bvid": "BV1"})

        assert response == "response"
        assert captured["params"]["wts"] == "123"
        assert captured["params"]["w_rid"] == "abc"

    async def test_retry_on_5xx(self, monkeypatch):
        manager = HTTPClientManager()
        responses = [httpx.Response(503), httpx.Response(200)]
        calls = 0

        class Client:
            async def request(self, method, url, **kwargs):
                nonlocal calls
                calls += 1
                return responses.pop(0)

        monkeypatch.setattr(manager, "_get_client", AsyncMock(return_value=Client()))
        monkeypatch.setattr("src.crawler.http_client.asyncio.sleep", AsyncMock())
        response = await manager.get("https://example.test", scene="video")

        assert response.status_code == 200
        assert calls == 2

    async def test_retry_on_bilibili_business_rate_limit(self, monkeypatch):
        manager = HTTPClientManager()
        responses = [
            httpx.Response(200, json={"code": -799, "message": "请求过于频繁"}),
            httpx.Response(200, json={"code": 0, "data": {"ok": True}}),
        ]
        calls = 0

        class Client:
            async def request(self, method, url, **kwargs):
                nonlocal calls
                calls += 1
                return responses.pop(0)

        monkeypatch.setattr(manager, "_get_client", AsyncMock(return_value=Client()))
        monkeypatch.setattr("src.crawler.http_client.asyncio.sleep", AsyncMock())
        response = await manager.get("https://example.test", scene="user")

        assert response.json()["code"] == 0
        assert calls == 2

    async def test_cache_hit_avoids_http(self):
        service = BiliVideoService()
        service.cache = AsyncMock()
        service.cache.get.return_value = {"bvid": "BV1", "title": "cached"}
        service.client = AsyncMock()

        result = await service.get_video_meta("BV1")

        assert result.bvid == "BV1"
        assert result.title == "cached"
        service.client.get_video_meta.assert_not_awaited()
        service.cache.set.assert_not_awaited()

    async def test_interaction_degrades_when_one_endpoint_fails(self, monkeypatch):
        client = BiliClient()
        responses = [
            httpx.Response(200, json={"code": -799, "message": "请求过于频繁"}),
            httpx.Response(200, json={"code": 0, "data": [{
                "bvid": "BV2", "aid": 2, "title": "推荐", "owner": {"name": "UP"},
                "stat": {"view": 10, "danmaku": 1}, "rcmd_reason": "测试",
            }]}),
        ]

        async def signed_get(*args, **kwargs):
            return responses.pop(0)

        monkeypatch.setattr(client, "_signed_get", signed_get)
        result = await client.get_interaction("BV1", 1)

        assert result.has_liked is False
        assert len(result.related_videos) == 1
        assert result.related_videos[0].reason == "测试"

    async def test_parse_danmaku_protobuf(self):
        reply = _build_danmaku_message_class()()
        elem = reply.elems.add()
        elem.content = "hello"
        elem.progress = 1500
        elem.fontsize = 25
        elem.color = 16777215
        elem.ctime = 1_700_000_000

        danmakus = await BiliClient()._parse_danmaku_protobuf(reply.SerializeToString())

        assert len(danmakus) == 1
        assert danmakus[0].content == "hello"
        assert danmakus[0].time_point == 1.5
        assert danmakus[0].font_size == 25
        assert danmakus[0].color == 16777215
        assert danmakus[0].send_time is not None
