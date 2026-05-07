"""Thin Futu OpenD client used by the HK/US paper-trading arena.

Wraps `OpenQuoteContext` for two operations:

* `get_snapshots(codes)` — returns a per-code dict that includes
  `last_price`, `lot_size`, `update_time` (region-local: HKT for HK,
  ET for US), `prev_close_price`, and `suspension`. Used both for
  pending-order matching and for live portfolio mark-to-market.
* `request_trading_days(market, start, end)` — returns the set of
  trading dates Futu reports for the given market, used by the HK/US
  region arenas as their session calendars.

The connection is opened lazily and reused across calls.
"""

import threading
from datetime import date, datetime
from logging import getLogger

from quant_arena.errors import ServiceError

logger = getLogger(__name__)


_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "code",
    "last_price",
    "prev_close_price",
    "open_price",
    "high_price",
    "low_price",
    "ask_price",
    "bid_price",
    "lot_size",
    "suspension",
    "update_time",
)


class FutumooService:
    """One process-wide Futu `OpenQuoteContext`, lazily connected."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._quote_ctx = None  # futu.OpenQuoteContext, lazy

    def _ensure_quote_ctx(self):
        if self._quote_ctx is not None:
            return self._quote_ctx
        with self._lock:
            if self._quote_ctx is None:
                from futu import OpenQuoteContext

                self._quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
                logger.info(
                    "Futumoo quote context connected to %s:%d", self.host, self.port
                )
        return self._quote_ctx

    def get_snapshots(self, codes: list[str]) -> dict[str, dict]:
        """Return `{code: row}` for each requested symbol.

        `row` is a plain dict with the subset of columns we use elsewhere
        (see `_SNAPSHOT_FIELDS`). Codes whose row carries no usable
        last price are omitted. Raises `ServiceError` if OpenD reports
        a non-OK return code.
        """
        if not codes:
            return {}
        ctx = self._ensure_quote_ctx()
        ret, data = ctx.get_market_snapshot(list(codes))
        if ret != 0:
            raise ServiceError(f"futu get_market_snapshot failed: {data}")
        out: dict[str, dict] = {}
        for _, row in data.iterrows():
            code = str(row["code"])
            try:
                last_price = float(row["last_price"])
            except (TypeError, ValueError):
                continue
            if last_price <= 0:
                continue
            entry: dict = {"code": code, "last_price": last_price}
            for field in _SNAPSHOT_FIELDS:
                if field in ("code", "last_price"):
                    continue
                if field in row.index:
                    entry[field] = row[field]
            out[code] = entry
        return out

    def get_last_prices(self, codes: list[str]) -> dict[str, float]:
        """Convenience wrapper returning only `{code: last_price}`."""
        return {code: float(row["last_price"]) for code, row in self.get_snapshots(codes).items()}

    def request_trading_days(
        self, market: str, start: date, end: date
    ) -> set[date]:
        """Return the set of trading dates Futu reports for `[start, end]`.

        `market` is one of the strings supported by Futu's `TradeDateMarket`
        enum — for our purposes, `"HK"` or `"US"`. Raises `ServiceError`
        on a non-OK return code; callers are expected to catch and fall
        back to a Mon–Fri heuristic when OpenD is unavailable.
        """
        ctx = self._ensure_quote_ctx()
        ret, data = ctx.request_trading_days(
            market=market, start=start.isoformat(), end=end.isoformat()
        )
        if ret != 0:
            raise ServiceError(f"futu request_trading_days({market}) failed: {data}")
        days: set[date] = set()
        if not data:
            return days
        for entry in data:
            raw = entry.get("time") if isinstance(entry, dict) else None
            if not raw:
                continue
            try:
                days.add(datetime.fromisoformat(str(raw)[:10]).date())
            except ValueError:
                continue
        return days

    def close(self) -> None:
        with self._lock:
            if self._quote_ctx is not None:
                try:
                    self._quote_ctx.close()
                except Exception:
                    logger.exception("Error closing Futu quote context")
                self._quote_ctx = None
