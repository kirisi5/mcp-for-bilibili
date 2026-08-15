"""
B站视频音频下载器。

原理：B站视频的音频和视频是分离存储的（DASH 格式）。
通过 /x/player/playurl API（fnval=16）获取音频流 URL，分片下载。
"""

import asyncio
import tempfile
import os
from typing import Optional

import httpx
from src.config import settings
from src.crawler.http_client import get_http_client


class AudioDownloader:
    """B站音频下载器"""

    def __init__(self):
        self._http = get_http_client()

    async def download_audio(self, bvid: str, cid: int, output_path: Optional[str] = None) -> str:
        """
        下载视频的音频流。

        工作流程：
        1. 调用 playurl API (fnval=16) 获取 DASH 音频流 URL
        2. 选择最高音质音频
        3. 流式下载到本地文件
        4. 检查文件大小限制
        """
        # 步骤 1：获取音频流 URL
        resp = await self._http.get(
            f"{settings.BILI_MAIN_API}/x/player/playurl",
            params={"bvid": bvid, "cid": cid, "fnval": 16, "qn": 0, "fourk": 1},
            scene="video",
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取音频流失败: {data.get('message')}")

        dash = data.get("data", {}).get("dash", {})
        audio_streams = dash.get("audio", [])
        if not audio_streams:
            raise RuntimeError("该视频无可用音频流（可能为纯图片视频）")

        # 选择最高音质
        audio_streams.sort(key=lambda x: x.get("id", 0), reverse=True)
        best_audio = audio_streams[0]
        url = best_audio.get("base_url") or best_audio.get("baseUrl", "")
        backup_urls = best_audio.get("backup_url", []) or best_audio.get("backupUrl", [])

        # 步骤 2：确定输出路径
        if output_path is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".m4a", delete=False)
            output_path = tmp.name
            tmp.close()

        # 步骤 3：流式下载（支持备用 URL）
        all_urls = [url] + backup_urls
        downloaded = 0

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=30.0),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/",
                     "Origin": "https://www.bilibili.com"},
        ) as client:
            for try_url in all_urls:
                if not try_url:
                    continue
                try:
                    async with client.stream("GET", try_url) as stream_resp:
                        if stream_resp.status_code != 200:
                            continue
                        with open(output_path, "wb") as f:
                            async for chunk in stream_resp.aiter_bytes(1024 * 1024):
                                f.write(chunk)
                                downloaded += len(chunk)
                        break
                except Exception:
                    continue

        if downloaded == 0:
            raise RuntimeError("音频下载失败：所有 URL 均不可用")
        if os.path.getsize(output_path) == 0:
            raise RuntimeError("音频下载失败：文件为空")

        return output_path


_audio_downloader: Optional[AudioDownloader] = None

def get_audio_downloader() -> AudioDownloader:
    global _audio_downloader
    if _audio_downloader is None:
        _audio_downloader = AudioDownloader()
    return _audio_downloader