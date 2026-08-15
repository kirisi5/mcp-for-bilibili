"""
WBI 动态签名器 — B站 API 反爬核心

工作流程：
1. 从 /x/web-interface/nav 获取 img_key 和 sub_key
2. 拼接后用 MIXIN_KEY_ENC_TAB 打乱 → 取前 32 位 → mixin_key
3. 请求参数排序拼接 + mixin_key → MD5 → w_rid
4. 每 23 小时自动刷新 mixin_key（key 有时效性）

MIXIN_KEY_ENC_TAB 来源：B站前端 JavaScript 混淆代码中的常量表
"""

import hashlib
import time
import asyncio
from typing import Any

import httpx
from src.config import settings

# 映射表（来自 B站前端混淆代码，稳定不变）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 57, 62, 34, 36, 20, 6, 59, 11, 63, 52, 44,
]


def _get_mixin_key(raw_key: str) -> str:
    """对原始 key 使用映射表打乱，取前 32 位作为 mixin_key"""
    return "".join(raw_key[i] for i in MIXIN_KEY_ENC_TAB if i < len(raw_key))[:32]


class WbiSigner:
    """WBI 签名器单例"""

    def __init__(self):
        self._mixin_key: str | None = None
        self._last_refresh: float = 0
        self._lock = asyncio.Lock()
        self._refresh_interval = 23 * 3600  # 23 小时

    async def _fetch_keys(self) -> tuple[str, str]:
        """从 B站 nav 接口获取 img_key 和 sub_key"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.bilibili.com/x/web-interface/nav",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"},
                cookies=settings.bili_cookies if settings.is_authenticated else None,
            )
            data = resp.json()
            wbi_img = data["data"]["wbi_img"]
            # wbi_img 格式: {"img_url": ".../xxx.png", "sub_url": ".../yyy.png"}
            # 从 URL 中提取文件名作为 key
            img_key = wbi_img["img_url"].split("/")[-1].split(".")[0]
            sub_key = wbi_img["sub_url"].split("/")[-1].split(".")[0]
            return img_key, sub_key

    async def _ensure_mixin_key(self):
        """确保 mixin_key 有效（过期自动刷新）"""
        now = time.time()
        if self._mixin_key is None or (now - self._last_refresh) > self._refresh_interval:
            async with self._lock:
                if self._mixin_key is None or (now - self._last_refresh) > self._refresh_interval:
                    img_key, sub_key = await self._fetch_keys()
                    self._mixin_key = _get_mixin_key(img_key + sub_key)
                    self._last_refresh = time.time()

    async def sign_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        对请求参数进行 WBI 签名。

        签名步骤：
        1. 添加 wts（当前秒级时间戳）
        2. 按键名字母顺序排序
        3. 拼接成 foo=bar&baz=qux 格式
        4. MD5(query + mixin_key) → w_rid
        """
        await self._ensure_mixin_key()
        assert self._mixin_key is not None
        signed = dict(params)
        signed["wts"] = str(int(time.time()))
        sorted_items = sorted(signed.items(), key=lambda x: x[0])
        query = "&".join(f"{k}={v}" for k, v in sorted_items)
        w_rid = hashlib.md5((query + self._mixin_key).encode("utf-8")).hexdigest()
        signed["w_rid"] = w_rid
        return signed


_wbi_signer: WbiSigner | None = None


def get_wbi_signer() -> WbiSigner:
    global _wbi_signer
    if _wbi_signer is None:
        _wbi_signer = WbiSigner()
    return _wbi_signer