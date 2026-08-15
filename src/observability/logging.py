"""
可观测性模块 — 日志系统（基于 loguru）

═══════════════════════════════════════════════════════════════
日志策略：
1. 控制台输出：彩色结构化日志（开发用）
2. 文件滚动：按大小/时间滚动，保留 7 天
3. JSON 格式：生产环境用 JSON 便于日志采集（ELK/Loki）
4. 关键字段：trace_id, elapsed, status_code, url
═══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger as _logger

from src.config.settings import settings, PROJECT_ROOT


def setup_logging():
    """
    初始化日志系统 — 在应用启动时调用一次

    输出目标：
    - stderr: 控制台（彩色）
    - logs/app.log: 所有级别（JSON，按 100MB 滚动）
    - logs/error.log: 仅 ERROR+（JSON，按 100MB 滚动）
    """
    # 移除默认 handler
    _logger.remove()

    # 确保日志目录存在
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    # 控制台输出
    _logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ) if settings.log_format == "text" else (
            '{{"time":"{time:YYYY-MM-DD HH:mm:ss.SSS}","level":"{level}","name":"{name}",'
            '"function":"{function}","line":{line},"message":"{message}"}}'
        ),
        colorize=True,
    )

    # 文件：所有级别
    _logger.add(
        settings.log_dir / "app_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        format=(
            '{{"time":"{time:YYYY-MM-DD HH:mm:ss.SSS}","level":"{level}","name":"{name}",'
            '"function":"{function}","line":{line},"message":"{message}"}}'
        ),
        rotation="100 MB",       # 按大小滚动
        retention="7 days",      # 保留 7 天
        compression="gz",        # 压缩旧日志
        encoding="utf-8",
    )

    # 文件：仅错误
    _logger.add(
        settings.log_dir / "error_{time:YYYY-MM-DD}.log",
        level="ERROR",
        format=(
            '{{"time":"{time:YYYY-MM-DD HH:mm:ss.SSS}","level":"{level}","name":"{name}",'
            '"function":"{function}","line":{line},"message":"{message}"}}'
        ),
        rotation="100 MB",
        retention="30 days",
        compression="gz",
        encoding="utf-8",
    )

    _logger.info(f"日志系统已初始化 (level={settings.log_level}, dir={settings.log_dir})")


# 导出 logger 供其他模块使用
logger = _logger
