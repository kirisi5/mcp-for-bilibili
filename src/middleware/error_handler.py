"""
异常处理中间件 — 统一错误响应 + 敏感信息脱敏

三层错误分类：
1. MCPBusinessError — 业务错误 → 透传给客户端
2. MCPSystemError  — 系统错误 → 返回通用提示
3. 未捕获异常       — 意外错误 → 详细日志，通用提示
"""

import functools
import re
import uuid
from collections.abc import Awaitable, Callable

from loguru import logger

# 敏感字段脱敏模式
_SENSITIVE_PATTERNS = [
    (re.compile(r"SESSDATA=[^;]+", re.IGNORECASE), "SESSDATA=***"),
    (re.compile(r"bili_jct=[^;]+", re.IGNORECASE), "bili_jct=***"),
    (re.compile(r"buvid3=[^;]+", re.IGNORECASE), "buvid3=***"),
    (re.compile(r'"SESSDATA":\s*"[^"]*"', re.IGNORECASE), '"SESSDATA": "***"'),
]


class MCPBusinessError(Exception):
    """业务异常 — 消息会直接返回给客户端"""
    code: str

    def __init__(self, message: str, code: str = "BUSINESS_ERROR"):
        self.code = code; super().__init__(message)


class MCPSystemError(Exception):
    """系统异常 — 客户端只看到通用提示"""
    detail: str

    def __init__(self, message: str, detail: str = ""):
        self.detail = detail; super().__init__(message)


def sanitize_error_message(message: str) -> str:
    """脱敏 — 移除错误消息中的 Cookie/Token"""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        message = pattern.sub(replacement, message)
    return message


def with_error_handler(func: Callable[..., Awaitable[object]]) -> Callable[..., Awaitable[object]]:
    """
    统一异常处理装饰器。
    用法：装饰所有 MCP tool/handler 函数
        @with_error_handler
        async def my_tool(...): ...
    """

    @functools.wraps(func)
    async def wrapper(*args: object, **kwargs: object) -> object:
        trace_id = uuid.uuid4().hex[:12]
        try:
            return await func(*args, **kwargs)
        except MCPBusinessError as e:
            logger.warning(f"[{trace_id}] 业务错误: {e}")
            return {"error": True, "code": e.code, "message": str(e), "trace_id": trace_id}
        except MCPSystemError as e:
            logger.error(f"[{trace_id}] 系统错误: {sanitize_error_message(str(e))}")
            if e.detail:
                logger.error(f"[{trace_id}] 详细: {sanitize_error_message(e.detail)}")
            return {"error": True, "code": "SYSTEM_ERROR",
                    "message": "服务暂时不可用，请稍后重试", "trace_id": trace_id}
        except Exception as e:
            logger.opt(exception=True).error(f"[{trace_id}] 未预期异常: {sanitize_error_message(str(e))}")
            return {"error": True, "code": "INTERNAL_ERROR",
                    "message": "服务内部错误", "trace_id": trace_id}
    return wrapper
