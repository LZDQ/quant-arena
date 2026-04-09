"""Shared Shanghai-local clock utilities."""

from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def now_shanghai() -> datetime:
    """Return the current Shanghai-local datetime."""

    return datetime.now(SHANGHAI_TZ)
