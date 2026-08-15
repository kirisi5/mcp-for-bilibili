"""
语音转文字（ASR）— 基于 faster-whisper

faster-whisper：OpenAI Whisper 的 CTranslate2 重新实现，速度提升 4 倍，内存减半。

模型大小与精度：
    tiny   (39MB)  — 中文勉强
    base   (74MB)  — 中文可用 ← 本项目默认
    small  (244MB) — 中文良好
    medium (769MB) — 优秀
    large  (1.5GB) — 最佳

首次运行自动下载模型（缓存到 ~/.cache/huggingface/hub/）
"""

import asyncio
import importlib.util
import os
from typing import TYPE_CHECKING, Protocol, cast

from src.config import settings
from src.models.schemas import SubtitleResult, SubtitleItem

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


class _CudaApi(Protocol):
    def is_available(self) -> bool: ...


class _TorchModule(Protocol):
    cuda: _CudaApi


def _cuda_available() -> bool:
    """检测 CUDA 是否可用（torch 为可选依赖，缺失时回退 CPU）"""
    if importlib.util.find_spec("torch") is None:
        return False
    torch = cast(_TorchModule, cast(object, importlib.import_module("torch")))
    return torch.cuda.is_available()


class WhisperTranscriber:
    """faster-whisper 转录器单例"""

    def __init__(self) -> None:
        self._model: WhisperModel | None = None
        self._model_lock: asyncio.Lock = asyncio.Lock()

    async def _load_model(self) -> None:
        """延迟加载 Whisper 模型（首次转录时才加载，避免 MCP Server 启动变慢）"""
        if self._model is not None:
            return
        async with self._model_lock:
            if self._model is not None:
                return
            from faster_whisper import WhisperModel

            device = settings.ASR_DEVICE
            if device == "cuda" and not _cuda_available():
                device = "cpu"

            self._model = WhisperModel(settings.ASR_MODEL_SIZE, device=device,
                                       compute_type=settings.ASR_COMPUTE_TYPE)

    async def transcribe(self, audio_path: str) -> SubtitleResult:
        """
        将音频转录为带时间戳的文本。

        关键设计：
        - 在线程池中执行（faster-whisper 是同步 API）
        - VAD（语音活动检测）自动跳过长段静音
        - beam_size=5 精度和速度平衡
        - **关键**：所有 segment 收集必须在 run_in_executor 内完成，避免跨线程访问 generator
        """
        await self._load_model()
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")
        assert self._model is not None

        # 定义同步包装函数：在线程池内部完成所有 generator 消费，返回安全数据
        def _transcribe_sync(path: str):
            model = self._model
            assert model is not None
            gen_segments, info = model.transcribe(
                path, language="zh", beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
            )
            # ⚠️ 必须在同一线程内消费完 generator，否则 CTranslate2 的 C++ 资源会失效
            items = [(round(seg.start, 2), round(seg.end, 2), seg.text.strip()) for seg in gen_segments]
            return items, info.language if info else "zh"

        items, lang = await asyncio.to_thread(_transcribe_sync, audio_path)

        subtitles = [SubtitleItem(from_time=start, to_time=end, content=text) for start, end, text in items]
        return SubtitleResult(subtitles=subtitles, source="asr_transcription", language=lang)

    async def transcribe_with_fallback(self, audio_path: str, timeout: float = 300.0) -> SubtitleResult:
        """带超时保护的转录（长篇视频转录可能耗时）"""
        try:
            return await asyncio.wait_for(self.transcribe(audio_path), timeout=timeout)
        except asyncio.TimeoutError:
            return SubtitleResult(subtitles=[], source="asr_transcription", language="zh")


_transcriber: WhisperTranscriber | None = None

def get_transcriber() -> WhisperTranscriber:
    global _transcriber
    if _transcriber is None:
        _transcriber = WhisperTranscriber()
    return _transcriber


async def safe_asr_transcribe(bvid: str, cid: int) -> SubtitleResult | None:
    """
    一站式：下载音频 + 转录 + 清理临时文件。
    这是给 MCP 工具和 Service 层使用的顶层函数。
    """
    from src.asr.audio_downloader import get_audio_downloader
    temp_file = None
    try:
        downloader = get_audio_downloader()
        transcriber = get_transcriber()
        temp_file = await downloader.download_audio(bvid, cid)
        result = await transcriber.transcribe_with_fallback(temp_file)
        return result
    except Exception:
        return None
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except Exception:
                pass
