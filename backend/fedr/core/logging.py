from __future__ import annotations

import logging
import sys

import structlog

from fedr.security.redaction import structlog_redactor


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO), stream=sys.stdout, format="%(message)s"
    )
    for noisy in ("ccxt", "httpx", "httpcore", "websockets", "aiosqlite", "urllib3", "web3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog_redactor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str):
    return structlog.get_logger(name)
