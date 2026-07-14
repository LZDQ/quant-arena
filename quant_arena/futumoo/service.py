"""Thin Futu OpenD client used by the HK/US/CN paper-trading arena.

Wraps `OpenQuoteContext` for four operations:

* `get_snapshots(codes)` — returns a per-code dict that includes
  `last_price`, `lot_size`, `update_time` (region-local: HKT for HK,
  ET for US, China time for CN), `prev_close_price`, and `suspension`.
  Snapshots validate new orders and supply symbol metadata.
* `get_cached_snapshots(codes)` — serves MCP latest-price queries from a
  configurable per-symbol snapshot cache and fetches only cache misses.
* `subscribe_live_quotes(codes)` — keeps an LRU set of real-time QUOTE
  subscriptions and forwards `StockQuoteHandlerBase` pushes to the arena.
* `request_trading_days(market, start, end)` — returns the set of
  trading dates Futu reports for the given market, used by the Futumoo
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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from logging import getLogger
from math import isfinite

from pandas import DataFrame

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
    "ask_vol",
    "bid_vol",
    "lot_size",
    "volume",
    "turnover",
    "turnover_rate",
    "price_spread",
    "amplitude",
    "avg_price",
    "bid_ask_ratio",
    "volume_ratio",
    "sec_status",
    "suspension",
    "update_time",
)


@dataclass(slots=True)
class _QuoteSubscription:
    name: str | None
    subscribed_at: float


@dataclass(slots=True)
class _SnapshotCacheEntry:
    snapshot: dict[str, object] | None
    expires_at: float


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "N/A":
        return None
    return text


def _int_or_none(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except ValueError:
        return None


def _bool_from_sdk(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def _optional_bool_from_sdk(value: object) -> bool | None:
    if _text_or_none(value) is None:
        return None
    return _bool_from_sdk(value)


class FutumooService:
    """One process-wide Futu `OpenQuoteContext`, lazily connected.

    Connection failures are cached for `_CONNECT_FAILURE_BACKOFF_SECONDS`;
    during that window every call short-circuits with `ServiceError` so
    the event loop never sits inside `OpenQuoteContext()`'s retry loop.
    """

    _CONNECT_PROBE_TIMEOUT_SECONDS: float = 2.0
    _CONNECT_FAILURE_BACKOFF_SECONDS: float = 30.0
    _SUBSCRIPTION_LIMIT: int = 100
    _MIN_SUBSCRIPTION_SECONDS: float = 60.0

    def __init__(
        self,
        host: str,
        port: int,
        live_quote_cache_seconds: int = 60,
    ):
        self.host = host
        self.port = port
        self.live_quote_cache_seconds = live_quote_cache_seconds
        self._lock = threading.RLock()
        self._subscription_lock = threading.RLock()
        self._snapshot_cache_lock = threading.RLock()
        self._quote_ctx = None  # futu.OpenQuoteContext, lazy
        # Monotonic timestamp of the last failed connect attempt, used to
        # gate retries via `_CONNECT_FAILURE_BACKOFF_SECONDS`.
        self._last_connect_failure_at: float | None = None
        # Dict insertion order tracks least to most recently accessed.
        self._subscriptions: dict[str, _QuoteSubscription] = {}
        self._quote_names: dict[str, str] = {}
        self._snapshot_cache: dict[str, _SnapshotCacheEntry] = {}
        self._live_quote_handler: (
            Callable[[str, dict[str, object]], None] | None
        ) = None

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
            quote_ctx = None
            try:
                from futu import OpenQuoteContext

                quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
                self._install_quote_handler(quote_ctx)
                self._quote_ctx = quote_ctx
            except Exception as exc:
                if quote_ctx is not None:
                    try:
                        quote_ctx.close()
                    except Exception:
                        logger.exception("Error closing failed Futu quote context")
                self._last_connect_failure_at = now
                raise ServiceError(
                    f"Failed to open Futu quote context at {self.host}:{self.port}: {exc}"
                ) from exc
            self._last_connect_failure_at = None
            logger.info(
                "Futumoo quote context connected to %s:%d", self.host, self.port
            )
        return self._quote_ctx

    def _install_quote_handler(self, quote_ctx) -> None:
        from futu import RET_OK, StockQuoteHandlerBase

        service = self

        class QuoteHandler(StockQuoteHandlerBase):
            def on_recv_rsp(self, rsp_pb):
                ret_code, data = super().on_recv_rsp(rsp_pb)
                if ret_code == RET_OK and isinstance(data, DataFrame):
                    service._publish_quote_frame(data)
                elif ret_code != RET_OK:
                    logger.warning("Futu quote push failed: %s", data)
                return ret_code, data

        if quote_ctx.set_handler(QuoteHandler()) != RET_OK:
            raise ServiceError("Failed to install Futu real-time quote handler")

    def set_live_quote_handler(
        self,
        handler: Callable[[str, dict[str, object]], None] | None,
    ) -> None:
        self._live_quote_handler = handler

    def _publish_quote_frame(self, frame: DataFrame) -> None:
        for _, row in frame.iterrows():
            entry = self._quote_entry_from_row(row)
            if entry is None:
                continue
            code = str(entry["code"])
            with self._subscription_lock:
                subscription = self._subscriptions.get(code)
                if subscription is None:
                    continue
                name = _text_or_none(entry.get("name"))
                if name is not None:
                    subscription.name = name
                    self._quote_names[code] = name
                handler = self._live_quote_handler
            if handler is None:
                continue
            try:
                handler(code, entry)
            except Exception:
                logger.exception("Futu live quote handler failed for %s", code)

    @staticmethod
    def _quote_entry_from_row(row) -> dict[str, object] | None:
        if "code" not in row.index or "last_price" not in row.index:
            return None
        code = str(row["code"])
        try:
            last_price = float(row["last_price"])
        except (TypeError, ValueError):
            return None
        if not isfinite(last_price) or last_price <= 0:
            return None
        entry: dict[str, object] = {
            "code": code,
            "last_price": last_price,
        }
        if "name" in row.index:
            entry["name"] = row["name"]
        data_date = (
            _text_or_none(row["data_date"])
            if "data_date" in row.index
            else None
        )
        data_time = (
            _text_or_none(row["data_time"])
            if "data_time" in row.index
            else None
        )
        if data_date is not None and data_time is not None:
            entry["update_time"] = f"{data_date} {data_time}"
        return entry

    def get_snapshots(self, codes: list[str]) -> dict[str, dict[str, object]]:
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
        out: dict[str, dict[str, object]] = {}
        for _, row in data.iterrows():
            code = str(row["code"])
            try:
                last_price = float(row["last_price"])
            except (TypeError, ValueError):
                continue
            if not isfinite(last_price) or last_price <= 0:
                continue
            entry: dict[str, object] = {"code": code, "last_price": last_price}
            for field in _SNAPSHOT_FIELDS:
                if field in ("code", "last_price"):
                    continue
                if field in row.index:
                    entry[field] = row[field]
            out[code] = entry
        self._remember_quote_names(out)
        self._cache_snapshots(codes, out)
        return out

    def get_cached_snapshots(
        self,
        codes: list[str],
    ) -> dict[str, dict[str, object]]:
        """Return market snapshots, fetching only absent or expired symbols."""

        if not codes:
            return {}
        now = time.monotonic()
        cached: dict[str, dict[str, object]] = {}
        missing: list[str] = []
        with self._snapshot_cache_lock:
            expired_codes = [
                code
                for code, entry in self._snapshot_cache.items()
                if entry.expires_at <= now
            ]
            for code in expired_codes:
                self._snapshot_cache.pop(code, None)
            for code in codes:
                entry = self._snapshot_cache.get(code)
                if entry is None:
                    missing.append(code)
                    continue
                if entry.snapshot is not None:
                    cached[code] = dict(entry.snapshot)
        if missing:
            cached.update(self.get_snapshots(missing))
        return cached

    def _cache_snapshots(
        self,
        requested_codes: list[str],
        snapshots: dict[str, dict[str, object]],
    ) -> None:
        if self.live_quote_cache_seconds <= 0:
            return
        now = time.monotonic()
        expires_at = now + self.live_quote_cache_seconds
        with self._snapshot_cache_lock:
            expired_codes = [
                code
                for code, entry in self._snapshot_cache.items()
                if entry.expires_at <= now
            ]
            for code in expired_codes:
                self._snapshot_cache.pop(code, None)
            for code in requested_codes:
                snapshot = snapshots.get(code)
                self._snapshot_cache[code] = _SnapshotCacheEntry(
                    snapshot=dict(snapshot) if snapshot is not None else None,
                    expires_at=expires_at,
                )

    def _remember_quote_names(self, snapshots: dict[str, dict[str, object]]) -> None:
        with self._subscription_lock:
            for code, row in snapshots.items():
                name = _text_or_none(row.get("name"))
                if name is None:
                    continue
                self._quote_names[code] = name
                subscription = self._subscriptions.get(code)
                if subscription is not None:
                    subscription.name = name

    def subscribe_live_quotes(self, codes: list[str]) -> list[str]:
        """Subscribe to real-time QUOTE pushes, touching each symbol in the LRU."""

        if not codes:
            return []
        quote_ctx = self._ensure_quote_ctx()
        from futu import RET_OK, SubType

        subscribed: list[str] = []
        for raw_code in codes:
            code = raw_code.strip()
            if not code or code in subscribed:
                continue
            with self._subscription_lock:
                current = self._subscriptions.pop(code, None)
                if current is not None:
                    self._subscriptions[code] = current
                    subscribed.append(code)
                    continue
                if len(self._subscriptions) >= self._SUBSCRIPTION_LIMIT:
                    self._evict_lru_subscription(quote_ctx, SubType.QUOTE, RET_OK)
                subscription = _QuoteSubscription(
                    name=self._quote_names.get(code),
                    subscribed_at=time.monotonic(),
                )
                # Register locally first so an immediate first push cannot race
                # ahead of the callback's membership check.
                self._subscriptions[code] = subscription
                try:
                    ret_code, message = quote_ctx.subscribe(
                        [code],
                        [SubType.QUOTE],
                        is_first_push=True,
                        subscribe_push=True,
                    )
                except Exception:
                    self._subscriptions.pop(code, None)
                    raise
                if ret_code != RET_OK:
                    self._subscriptions.pop(code, None)
                    raise ServiceError(
                        f"Futu quote subscription failed for {code}: {message}"
                    )
                subscribed.append(code)
        return subscribed

    def _evict_lru_subscription(
        self,
        quote_ctx,
        quote_subtype: str,
        ret_ok: int,
    ) -> None:
        code, subscription = next(iter(self._subscriptions.items()))
        age = time.monotonic() - subscription.subscribed_at
        if age < self._MIN_SUBSCRIPTION_SECONDS:
            retry_after = self._MIN_SUBSCRIPTION_SECONDS - age
            raise ServiceError(
                "Futu's 100-symbol subscription limit is full and the least "
                f"recently used symbol {code} cannot be evicted for another "
                f"{retry_after:.1f} seconds."
            )
        ret_code, message = quote_ctx.unsubscribe([code], [quote_subtype])
        if ret_code != ret_ok:
            raise ServiceError(f"Futu quote unsubscribe failed for {code}: {message}")
        self._subscriptions.pop(code, None)

    def get_subscription_status(self) -> dict[str, object]:
        with self._subscription_lock:
            latest = list(reversed(self._subscriptions.items()))[:3]
            return {
                "subscribed_count": len(self._subscriptions),
                "subscription_limit": self._SUBSCRIPTION_LIMIT,
                "latest_accessed_symbols": [
                    {"code": code, "name": subscription.name}
                    for code, subscription in latest
                ],
            }

    def get_last_prices(self, codes: list[str]) -> dict[str, float]:
        """Convenience wrapper returning only `{code: last_price}`."""
        return {
            code: float(row["last_price"])
            for code, row in self.get_snapshots(codes).items()
        }

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
        rows = data.to_dict(orient="records") if isinstance(data, DataFrame) else data
        for entry in rows:
            raw = entry.get("time") if isinstance(entry, dict) else None
            if not raw:
                continue
            try:
                days.add(datetime.fromisoformat(str(raw)[:10]).date())
            except ValueError:
                continue
        return days

    def get_user_info(self) -> dict[str, object]:
        """Return the logged-in Futu OpenD user plus quote/trade login state."""
        ctx = self._ensure_quote_ctx()
        from futu import UserInfoField

        ret, user_data = ctx.get_user_info(
            info_field=[
                UserInfoField.BASIC,
                UserInfoField.API,
                UserInfoField.QOTRIGHT,
                UserInfoField.DISCLAIMER,
                UserInfoField.UPDATE,
                UserInfoField.WEBKEY,
            ]
        )
        if ret != 0:
            raise ServiceError(f"futu get_user_info failed: {user_data}")
        if not isinstance(user_data, dict):
            raise ServiceError("futu get_user_info returned an unexpected payload")
        ret, state_data = ctx.get_global_state()
        if ret != 0:
            raise ServiceError(f"futu get_global_state failed: {state_data}")
        if not isinstance(state_data, dict):
            raise ServiceError("futu get_global_state returned an unexpected payload")

        return {
            "nick_name": _text_or_none(user_data.get("nick_name")),
            "avatar_url": _text_or_none(user_data.get("avatar_url")),
            "user_id": _text_or_none(user_data.get("user_id")),
            "login_user_id": _text_or_none(ctx.get_login_user_id()),
            "user_attr": _text_or_none(user_data.get("user_attr")),
            "api_level": _text_or_none(user_data.get("api_level")),
            "hk_qot_right": _text_or_none(user_data.get("hk_qot_right")),
            "hk_option_qot_right": _text_or_none(
                user_data.get("hk_option_qot_right")
            ),
            "hk_future_qot_right": _text_or_none(
                user_data.get("hk_future_qot_right")
            ),
            "us_qot_right": _text_or_none(user_data.get("us_qot_right")),
            "us_option_qot_right": _text_or_none(
                user_data.get("us_option_qot_right")
            ),
            "us_future_qot_right": _text_or_none(
                user_data.get("us_future_qot_right")
            ),
            "cn_qot_right": _text_or_none(user_data.get("cn_qot_right")),
            "sg_future_qot_right": _text_or_none(
                user_data.get("sg_future_qot_right")
            ),
            "jp_future_qot_right": _text_or_none(
                user_data.get("jp_future_qot_right")
            ),
            "us_future_qot_right_cme": _text_or_none(
                user_data.get("us_future_qot_right_cme")
            ),
            "us_future_qot_right_cbot": _text_or_none(
                user_data.get("us_future_qot_right_cbot")
            ),
            "us_future_qot_right_nymex": _text_or_none(
                user_data.get("us_future_qot_right_nymex")
            ),
            "us_future_qot_right_comex": _text_or_none(
                user_data.get("us_future_qot_right_comex")
            ),
            "us_future_qot_right_cboe": _text_or_none(
                user_data.get("us_future_qot_right_cboe")
            ),
            "is_need_agree_disclaimer": _optional_bool_from_sdk(
                user_data.get("is_need_agree_disclaimer")
            ),
            "update_type": _text_or_none(user_data.get("update_type")),
            "web_key": _text_or_none(user_data.get("web_key")),
            "sub_quota": _int_or_none(user_data.get("sub_quota")),
            "history_kl_quota": _int_or_none(user_data.get("history_kl_quota")),
            "qot_logined": _bool_from_sdk(state_data.get("qot_logined")),
            "trd_logined": _bool_from_sdk(state_data.get("trd_logined")),
            "program_status_type": _text_or_none(state_data.get("program_status_type")),
            "program_status_desc": _text_or_none(state_data.get("program_status_desc")),
            "server_ver": _text_or_none(state_data.get("server_ver")),
            "market_hk": _text_or_none(state_data.get("market_hk")),
            "market_us": _text_or_none(state_data.get("market_us")),
            "market_sh": _text_or_none(state_data.get("market_sh")),
            "market_sz": _text_or_none(state_data.get("market_sz")),
        }

    def close(self) -> None:
        with self._lock:
            if self._quote_ctx is not None:
                try:
                    self._quote_ctx.close()
                except Exception:
                    logger.exception("Error closing Futu quote context")
                self._quote_ctx = None
        with self._subscription_lock:
            self._subscriptions.clear()
        with self._snapshot_cache_lock:
            self._snapshot_cache.clear()
