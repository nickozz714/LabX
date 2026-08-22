"""component_logging.py — tiny structured-logging shim.

ND3X has a full component.logging module (structured `.infox`/`.warningx` calls
with kwargs); LabX doesn't need that machinery, but a lot of ported code calls
`log.infox(...)`/`log.warningx(...)`. This shim gives stdlib `logging.Logger`
those two methods so ported code needs no rewriting.
"""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _CONFIGURED = True


def _kv_suffix(kwargs: dict) -> str:
    if not kwargs:
        return ""
    return " " + " ".join(f"{k}={v!r}" for k, v in kwargs.items())


class _StructuredLogger(logging.Logger):
    def infox(self, msg: str, **kwargs) -> None:
        self.info(msg + _kv_suffix(kwargs))

    def warningx(self, msg: str, **kwargs) -> None:
        self.warning(msg + _kv_suffix(kwargs))

    def errorx(self, msg: str, **kwargs) -> None:
        self.error(msg + _kv_suffix(kwargs))


logging.setLoggerClass(_StructuredLogger)


def get_logger(name: str) -> _StructuredLogger:
    _configure()
    return logging.getLogger(name)  # type: ignore[return-value]
