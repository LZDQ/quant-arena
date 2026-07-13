"""Shanghai-local clock utilities for the A-share arena."""

from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def now_shanghai() -> datetime:
    """Return the current Shanghai-local datetime."""

    return datetime.now(SHANGHAI_TZ)
