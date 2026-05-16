"""Thin Futu OpenD client used by the HK/US paper-trading arena.

Wraps `OpenQuoteContext` for two operations:

* `get_snapshots(codes)` — returns a per-code dict that includes
  `last_price`, `lot_size`, `update_time` (region-local: HKT for HK,
  ET for US), `prev_close_price`, and `suspension`. Used both for
  pending-order matching and for live portfolio mark-to-market.
* `request_trading_days(market, start, end)` — returns the set of
  trading dates Futu reports for the given market, used by the HK/US
  region arenas as their session calendars.

The connection is opened lazily and reused across calls. Because
`OpenQuoteContext()` retries connection synchronously and can stall
the calling thread for minutes when OpenD is down, every call site
goes through `_ensure_quote_ctx` which first does a fast TCP probe
and remembers the failure for `_CONNECT_FAILURE_BACKOFF`. While the
backoff window is active we raise `ServiceError` immediately instead
of attempting a fresh connection — keeping the FastAPI event loop
responsive and letting the user disable the arena even when OpenD is
unreachable.
"""

import socket
import threading
import time
from datetime import date, datetime
from logging import getLogger

from quant_arena.errors import ServiceError

logger = getLogger(__name__)


_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "code",
    "name",
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
    """One process-wide Futu `OpenQuoteContext`, lazily connected.

    Connection failures are cached for `_CONNECT_FAILURE_BACKOFF_SECONDS`;
    during that window every call short-circuits with `ServiceError` so
    the event loop never sits inside `OpenQuoteContext()`'s retry loop.
    """

    _CONNECT_PROBE_TIMEOUT_SECONDS: float = 2.0
    _CONNECT_FAILURE_BACKOFF_SECONDS: float = 30.0

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._quote_ctx = None  # futu.OpenQuoteContext, lazy
        # Monotonic timestamp of the last failed connect attempt, used to
        # gate retries via `_CONNECT_FAILURE_BACKOFF_SECONDS`.
        self._last_connect_failure_at: float | None = None

    def _probe_port(self) -> bool:
        """Quick TCP probe: open and close a socket within the timeout.

        OpenD's gateway accepts TCP connections on its configured port; if
        the port refuses the connection or the host is unreachable, we
        know `OpenQuoteContext()` will fail too and skip its retry storm.
        """
        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=self._CONNECT_PROBE_TIMEOUT_SECONDS,
            ):
                return True
        except OSError:
            return False

    def _ensure_quote_ctx(self):
        if self._quote_ctx is not None:
            return self._quote_ctx
        with self._lock:
            if self._quote_ctx is not None:
                return self._quote_ctx
            now = time.monotonic()
            if (
                self._last_connect_failure_at is not None
                and now - self._last_connect_failure_at
                < self._CONNECT_FAILURE_BACKOFF_SECONDS
            ):
                raise ServiceError(
                    f"Futu OpenD at {self.host}:{self.port} is unreachable; "
                    f"backing off for "
                    f"{self._CONNECT_FAILURE_BACKOFF_SECONDS:.0f}s before retrying."
                )
            if not self._probe_port():
                self._last_connect_failure_at = now
                raise ServiceError(
                    f"Futu OpenD at {self.host}:{self.port} is not accepting "
                    f"TCP connections (probe timed out after "
                    f"{self._CONNECT_PROBE_TIMEOUT_SECONDS:.0f}s)."
                )
            try:
                from futu import OpenQuoteContext

                self._quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
            except Exception as exc:
                self._last_connect_failure_at = now
                raise ServiceError(
                    f"Failed to open Futu quote context at {self.host}:{self.port}: {exc}"
                ) from exc
            self._last_connect_failure_at = None
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
        on a non-OK return code, with the underlying OpenD error description
        included in the message so callers can log it; callers are expected
        to catch and fall back to a Mon–Fri heuristic when OpenD is
        unavailable. The success payload is a list of dicts shaped like
        `[{"time": "2020-04-01", "trade_date_type": "WHOLE"}]` per the SDK.
        """
        ctx = self._ensure_quote_ctx()
        ret, data = ctx.request_trading_days(
            market=market, start=start.isoformat(), end=end.isoformat()
        )
        if ret != 0:
            raise ServiceError(
                f"futu request_trading_days(market={market!r}, "
                f"{start.isoformat()}..{end.isoformat()}) failed: {data}"
            )
        days: set[date] = set()
        if not data:
            return days
        # Some SDK versions return a DataFrame; treat both shapes uniformly.
        rows = data.to_dict(orient="records") if hasattr(data, "to_dict") else data
        for entry in rows:
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
