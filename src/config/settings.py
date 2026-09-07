"""
统一配置管理 — 基于 pydantic-settings

设计原则：
1. 所有配置集中管理，一处修改全局生效
2. 支持环境变量覆盖（12-Factor App 原则）
3. 类型安全：每个配置项都有明确类型，IDE 可自动补全
4. 按功能分组：认证/缓存/限流/日志/ASR

使用方法：
    from src.config import settings
    print(settings.BILI_SESSDATA)  # 自动从 .env 读取
"""

from pydantic_settings.main import SettingsConfigDict


from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    """全局配置单例"""

    # ── B站认证（从浏览器 Cookie 获取，必填）──
    BILI_SESSDATA: str = ""
    BILI_BILI_JCT: str = ""
    BILI_BUVID3: str = ""

    # ── Redis 连接（可选）──
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = False

    # ── 缓存 TTL（秒）──
    CACHE_TTL_SHORT: int = 300      # 5分钟 — 评论、弹幕
    CACHE_TTL_MEDIUM: int = 1800    # 30分钟 — 视频元数据、字幕
    CACHE_TTL_LONG: int = 86400     # 24小时 — 用户信息

    # ── HTTP 客户端 ──
    HTTP_TIMEOUT: float = 30.0
    HTTP_MAX_CONNECTIONS: int = 50
    HTTP_MAX_RETRIES: int = 3

    # ── 限流配额（次/秒，按场景独立限流）──
    # 设计原理：不同 API 端点对服务器压力不同，给敏感端点更严格限制
    RATE_LIMIT_DEFAULT: float = 5.0
    RATE_LIMIT_VIDEO: float = 5.0
    RATE_LIMIT_SEARCH: float = 3.0    # 搜索是风控重点
    RATE_LIMIT_COMMENT: float = 2.0   # 评论接口最敏感
    RATE_LIMIT_DANMAKU: float = 3.0
    RATE_LIMIT_USER: float = 0.5     # 用户信息接口更容易触发风控

    # ── MCP Server ──
    MCP_SERVER_HOST: str = "0.0.0.0"
    MCP_SERVER_PORT: int = 8000

    # ── B站 API 基础 URL ──
    BILI_MAIN_API: str = "https://api.bilibili.com"
    BILI_PASSPORT_API: str = "https://passport.bilibili.com"
    BILI_GRPC_API: str = "https://grpc.biliapi.net"

    # ── ASR 语音识别 ──
    ASR_MODEL_SIZE: Literal["tiny", "base", "small", "medium", "large"] = "base"
    ASR_DEVICE: Literal["cpu", "cuda"] = "cpu"
    ASR_COMPUTE_TYPE: str = "int8"
    ASR_MAX_AUDIO_SIZE_MB: int = 100

    # ── 日志 ──
    LOG_LEVEL: Literal["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["json", "text"] = "text"
    LOG_DIR: Path = Path("logs")

    # ── pydantic-settings 配置 ──
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def bili_cookies(self) -> dict[str, str]:
        """格式化 B站 Cookies 为 httpx 可用的字典"""
        return {
            "SESSDATA": self.BILI_SESSDATA,
            "bili_jct": self.BILI_BILI_JCT,
            "buvid3": self.BILI_BUVID3,
        }

    @property
    def is_authenticated(self) -> bool:
        """检查是否配置了认证信息"""
        return bool(self.BILI_SESSDATA and self.BILI_BILI_JCT)


settings: Settings = Settings()  # 全局单例