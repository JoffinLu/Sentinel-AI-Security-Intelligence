"""
结构化日志

统一 JSON 格式输出，便于 Prometheus 日志采集与链路追踪。
每条日志自动附加时间戳、级别、模块名。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from .config import get_settings

_settings = get_settings()


def configure_logging() -> None:
    """初始化日志系统"""
    level = getattr(logging, _settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    # 第三方库降噪，避免日志刷屏
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "neo4j"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if _settings.log_format == "json":
        processors.append(structlog.processors.JSONRenderer(ensure_ascii=False))
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    获取带模块名的日志器

    Args:
        name: 模块名，通常传 __name__
    """
    return structlog.get_logger(name)
