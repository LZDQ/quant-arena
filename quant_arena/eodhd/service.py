"""EODHD-backed market-data service and live quote adapter."""

import asyncio
import json
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from importlib import resources
from importlib.metadata import PackageNotFoundError, version
from logging import getLogger
from pathlib import Path
import threading
import time as time_module
from urllib.parse import quote

import pandas as pd
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

from quant_arena.config import EODHDExchangeConfig
from quant_arena.errors import ServiceError

logger = getLogger(__name__)


_INTRADAY_COLUMNS = [
    "date",
    "datetime_utc",
    "timestamp",
    "gmtoffset",
    "symbol",
    "code",
    "exchange",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

_INTRADAY_DTYPES = {
    "date": "string",
    "datetime_utc": "string",
    "timestamp": "int64",
    "gmtoffset": "Int64",
    "symbol": "string",
    "code": "string",
    "exchange": "string",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "Int64",
}


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _utc_time_from_text(value: str) -> time:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"Expected UTC time in HH:MM format, got {value!r}") from exc
    return parsed.time()


def _field_from_mapping(row: dict[str, object], names: tuple[str, ...]) -> object:
    for name in names:
        if name in row:
            return row[name]
    return None


@dataclass(frozen=True, slots=True)
class _RuntimeBarSchedule:
    enabled: bool
    finalize_time_utc: time


@dataclass(frozen=True, slots=True)
class _RuntimeExchangeConfig:
    daily_bars: _RuntimeBarSchedule
    five_min_bars: _RuntimeBarSchedule
    target_date_offset_days: int


@dataclass(frozen=True, slots=True)
class _WebSocketTarget:
    endpoint: str
    wire_symbol: str
    code: str


@dataclass(slots=True)
class _WebSocketEndpointState:
    endpoint: str
    # Dict insertion order tracks least to most recently requested.
    desired: dict[str, str] = field(default_factory=dict)
    subscribed: set[str] = field(default_factory=set)
    pending_unsubscribe: set[str] = field(default_factory=set)
    stop_event: threading.Event = field(default_factory=threading.Event)
    snapshot_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class _EODHDWebSocketQuoteStream:
    _RECONNECT_BACKOFF_SECONDS = 5.0
    _RECV_TIMEOUT_SECONDS = 1.0
    _FIRST_QUOTE_TIMEOUT_SECONDS = 3.0

    def __init__(
        self,
        api_token: str,
        subscribe_limit: int,
        snapshot_handler: Callable[[str, dict[str, object]], None],
    ):
        self._api_token = api_token
        self._subscribe_limit = subscribe_limit
        self._snapshot_handler = snapshot_handler
        self._lock = threading.RLock()
        self._states: dict[str, _WebSocketEndpointState] = {}
        self._snapshots: dict[str, dict[str, object]] = {}

    def subscribe(self, targets: list[_WebSocketTarget]) -> None:
        with self._lock:
            for target in targets:
                state = self._state_for_target(target)
                if target.wire_symbol in state.desired:
                    del state.desired[target.wire_symbol]
                elif len(state.desired) >= self._subscribe_limit:
                    evicted_symbol = next(iter(state.desired))
                    evicted_code = state.desired.pop(evicted_symbol)
                    if evicted_symbol in state.subscribed:
                        state.pending_unsubscribe.add(evicted_symbol)
                    self._snapshots.pop(evicted_code, None)
                state.pending_unsubscribe.discard(target.wire_symbol)
                state.desired[target.wire_symbol] = target.code
                if state.thread is None or not state.thread.is_alive():
                    state.subscribed.clear()
                    state.pending_unsubscribe.clear()
                    state.stop_event.clear()
                    state.thread = threading.Thread(
                        target=self._run_endpoint,
                        args=(state,),
                        name=f"eodhd-websocket-{state.endpoint}",
                        daemon=True,
                    )
                    state.thread.start()

    def wait_for_snapshots(self, codes: list[str]) -> dict[str, dict[str, object]]:
        deadline = time_module.monotonic() + self._FIRST_QUOTE_TIMEOUT_SECONDS
        missing = set(codes)
        out: dict[str, dict[str, object]] = {}
        while missing:
            with self._lock:
                out = {
                    code: dict(snapshot)
                    for code, snapshot in self._snapshots.items()
                    if code in codes
                }
                missing = {code for code in codes if code not in out}
                events = [
                    state.snapshot_event
                    for state in self._states.values()
                ]
            if not missing:
                return out
            remaining = deadline - time_module.monotonic()
            if remaining <= 0 or not events:
                return out
            for event in events:
                event.wait(min(remaining, 0.25))
                event.clear()
                if time_module.monotonic() >= deadline:
                    break
        return out

    def close(self) -> None:
        with self._lock:
            states = list(self._states.values())
            for state in states:
                state.stop_event.set()
        current = threading.current_thread()
        for state in states:
            thread = state.thread
            if thread is not None and thread is not current and thread.is_alive():
                thread.join(timeout=2.0)

    def _state_for_target(self, target: _WebSocketTarget) -> _WebSocketEndpointState:
        state = self._states.get(target.endpoint)
        if state is not None:
            return state
        state = _WebSocketEndpointState(endpoint=target.endpoint)
        self._states[target.endpoint] = state
        return state

    def _run_endpoint(self, state: _WebSocketEndpointState) -> None:
        while not state.stop_event.is_set():
            try:
                url = (
                    "wss://ws.eodhistoricaldata.com/ws/"
                    f"{state.endpoint}?api_token={quote(self._api_token)}"
                )
                with connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    open_timeout=10,
                ) as connection:
                    self._sync_subscriptions(state, connection)
                    while not state.stop_event.is_set():
                        self._sync_subscriptions(state, connection)
                        try:
                            raw_message = connection.recv(
                                timeout=self._RECV_TIMEOUT_SECONDS
                            )
                        except TimeoutError:
                            continue
                        self._handle_message(state, raw_message)
            except ConnectionClosed:
                self._clear_subscriptions(state)
            except Exception:
                if not state.stop_event.is_set():
                    logger.exception(
                        "EODHD websocket stream failed for %s; reconnecting",
                        state.endpoint,
                    )
                self._clear_subscriptions(state)
            if not state.stop_event.is_set():
                state.stop_event.wait(self._RECONNECT_BACKOFF_SECONDS)

    def _sync_subscriptions(
        self, state: _WebSocketEndpointState, connection: ClientConnection
    ) -> None:
        with self._lock:
            symbols = sorted(state.pending_unsubscribe)
            if symbols:
                connection.send(
                    json.dumps(
                        {"action": "unsubscribe", "symbols": ",".join(symbols)}
                    )
                )
                state.subscribed.difference_update(symbols)
                state.pending_unsubscribe.difference_update(symbols)
            symbols = sorted(set(state.desired) - state.subscribed)
            if symbols:
                connection.send(
                    json.dumps({"action": "subscribe", "symbols": ",".join(symbols)})
                )
                state.subscribed.update(symbols)

    def _clear_subscriptions(self, state: _WebSocketEndpointState) -> None:
        with self._lock:
            state.subscribed.clear()
            state.pending_unsubscribe.clear()

    def _handle_message(
        self, state: _WebSocketEndpointState, raw_message: str | bytes
    ) -> None:
        if isinstance(raw_message, bytes):
            text = raw_message.decode("utf-8")
        else:
            text = raw_message
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON EODHD websocket message: %r", text)
            return
        if not isinstance(parsed, dict):
            return
        message = {str(key): value for key, value in parsed.items()}
        wire_symbol = _text_or_none(
            _field_from_mapping(message, ("s", "symbol", "code"))
        )
        if wire_symbol is None:
            return
        price = self._price_from_message(state.endpoint, message)
        if price is None or price <= 0:
            return
        timestamp = _float_or_none(
            _field_from_mapping(message, ("t", "timestamp", "time"))
        )
        update_time = self._update_time_from_timestamp(timestamp)
        with self._lock:
            code = state.desired.get(wire_symbol)
            if code is None:
                code = state.desired.get(wire_symbol.upper())
            if code is None:
                return
            snapshot: dict[str, object] = {
                "code": code,
                "last_price": price,
                "update_time": update_time.isoformat(),
            }
            self._snapshots[code] = snapshot
            state.snapshot_event.set()
        self._snapshot_handler(code, dict(snapshot))

    @staticmethod
    def _price_from_message(endpoint: str, message: dict[str, object]) -> float | None:
        if endpoint == "forex":
            bid = _float_or_none(_field_from_mapping(message, ("b", "bid")))
            ask = _float_or_none(_field_from_mapping(message, ("a", "ask")))
            if bid is not None and ask is not None and bid > 0 and ask > 0:
                return (bid + ask) / 2
            return bid or ask
        return _float_or_none(_field_from_mapping(message, ("p", "price", "last")))

    @staticmethod
    def _update_time_from_timestamp(timestamp: float | None) -> datetime:
        if timestamp is None:
            return datetime.now(timezone.utc)
        if timestamp > 9999999999:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, timezone.utc)


@dataclass(slots=True)
class EODHDCorporateAction:
    code: str
    exchange: str
    ex_date: date
    cash_dividend_per_share: float = 0.0
    dividend_currency: str | None = None
    split_ratio: float = 1.0
    split_text: str = ""
    dividend_period: str | None = None


class EODHDService:
    """EODHD all-in-one data persistence plus live quote snapshots."""

    def __init__(
        self,
        *,
        api_token: str,
        market_data_root: Path,
        exchanges: dict[str, EODHDExchangeConfig],
        websocket_subscribe_limit: int = 50,
    ):
        self.api_token = api_token
        self.market_data_root = market_data_root
        self.exchange_configs = self._normalize_exchanges(exchanges)
        self.exchanges = list(self.exchange_configs)
        self._enabled_exchanges = set(self.exchanges)
        self._code_names_by_exchange: dict[str, pd.DataFrame] = {}
        self._code_names: pd.DataFrame | None = None
        self._code_name_index: dict[str, str] | None = None
        self._latest_daily_frame: pd.DataFrame | None = None
        self._client = None  # eodhd.APIClient, created lazily
        self._live_quote_handler: Callable[[str, dict[str, object]], None] | None = None
        self._websocket_quotes = _EODHDWebSocketQuoteStream(
            api_token,
            websocket_subscribe_limit,
            self._publish_live_quote,
        )
        self.market_data_root.mkdir(parents=True, exist_ok=True)
        for exchange in self.exchanges:
            self._ensure_exchange_dirs(exchange)
        shutil.copyfile(
            resources.files("quant_arena.resources").joinpath("README-eodhd-market-data.md"),
            market_data_root / "README.md",
        )

    @staticmethod
    def _normalize_exchanges(
        exchanges: dict[str, EODHDExchangeConfig],
    ) -> dict[str, _RuntimeExchangeConfig]:
        normalized: dict[str, _RuntimeExchangeConfig] = {}
        for exchange_name, exchange_config in exchanges.items():
            if not exchange_config.enabled:
                continue
            exchange = exchange_name.strip().upper()
            if not exchange:
                continue
            normalized[exchange] = _RuntimeExchangeConfig(
                daily_bars=_RuntimeBarSchedule(
                    enabled=exchange_config.daily_bars.enabled,
                    finalize_time_utc=_utc_time_from_text(
                        exchange_config.daily_bars.finalize_utc
                    ),
                ),
                five_min_bars=_RuntimeBarSchedule(
                    enabled=exchange_config.five_min_bars.enabled,
                    finalize_time_utc=_utc_time_from_text(
                        exchange_config.five_min_bars.finalize_utc
                    ),
                ),
                target_date_offset_days=exchange_config.target_date_offset_days,
            )
        return normalized

    def _api_client(self):
        if self._client is None:
            from eodhd import APIClient

            self._client = APIClient(self.api_token)
        return self._client

    def close(self) -> None:
        self._websocket_quotes.close()

    def set_live_quote_handler(
        self,
        handler: Callable[[str, dict[str, object]], None] | None,
    ) -> None:
        self._live_quote_handler = handler

    def _publish_live_quote(self, code: str, snapshot: dict[str, object]) -> None:
        handler = self._live_quote_handler
        if handler is None:
            return
        try:
            handler(code, snapshot)
        except Exception:
            logger.exception("EODHD live quote handler failed for %s", code)

    def is_exchange_enabled(self, exchange: str) -> bool:
        return exchange.strip().upper() in self._enabled_exchanges

    def is_symbol_exchange_enabled(self, code: str) -> bool:
        _, exchange = self._split_symbol(code)
        return self.is_exchange_enabled(exchange)

    def _ensure_exchange_dirs(self, exchange: str) -> None:
        self._exchange_dir(exchange).mkdir(parents=True, exist_ok=True)
        self._daily_dir(exchange).mkdir(parents=True, exist_ok=True)
        self._five_min_dir(exchange).mkdir(parents=True, exist_ok=True)

    def _exchange_dir(self, exchange: str) -> Path:
        return self.market_data_root / exchange

    def _exchange_code_names_path(self, exchange: str) -> Path:
        return self._exchange_dir(exchange) / "code_names.csv"

    def _daily_dir(self, exchange: str) -> Path:
        return self._exchange_dir(exchange) / "daily"

    def _five_min_dir(self, exchange: str) -> Path:
        return self._exchange_dir(exchange) / "5min"

    def _daily_path(self, exchange: str, day: date) -> Path:
        return self._daily_dir(exchange) / f"{day.isoformat()}.csv"

    def _five_min_path(self, exchange: str, day: date) -> Path:
        return self._five_min_dir(exchange) / f"{day.isoformat()}.csv"

    def get_user_info(self) -> dict[str, object]:
        """Return configured EODHD identity/status for the page header."""
        return {
            "credential_status": self._credential_status(),
            "package_version": self._package_version(),
            "configured_exchanges": list(self.exchanges),
            "code_names_count": self._code_names_count(),
        }

    def _credential_status(self) -> str:
        return "configured" if self.api_token.strip() else "missing"

    @staticmethod
    def _package_version() -> str:
        try:
            return version("eodhd")
        except PackageNotFoundError:
            return "unknown"

    def _code_names_count(self) -> int:
        frame = self.get_code_names()
        return 0 if frame is None else len(frame)

    def get_code_names(self) -> pd.DataFrame | None:
        if self._code_names is None:
            frames: list[pd.DataFrame] = []
            for exchange in self.exchanges:
                frame = self._get_exchange_code_names(exchange)
                if frame is not None and not frame.empty:
                    frames.append(frame)
            if frames:
                self._code_names = self._combine_code_name_frames(frames)
        return self._code_names

    def get_code_name(self, code: str) -> str | None:
        if self._code_name_index is None:
            frame = self.get_code_names()
            if frame is None or frame.empty:
                self._code_name_index = {}
            else:
                self._code_name_index = dict(
                    zip(frame["symbol"].astype(str), frame["name"].astype(str))
                )
        return self._code_name_index.get(code)

    def get_code_metadata(self, code: str) -> dict[str, str | None]:
        frame = self.get_code_names()
        if frame is None or frame.empty:
            return {}
        matches = frame[frame["symbol"].astype(str) == code]
        if matches.empty:
            return {}
        record: dict[str, object] = {
            str(key): value for key, value in matches.iloc[0].to_dict().items()
        }
        return {
            "name": _text_or_none(self._field(record, ("name", "Name"))),
            "exchange": _text_or_none(self._field(record, ("exchange", "Exchange"))),
            "currency": _text_or_none(self._field(record, ("currency", "Currency"))),
            "type": _text_or_none(self._field(record, ("type", "Type"))),
            "country": _text_or_none(self._field(record, ("country", "Country"))),
        }

    def refresh_code_names(self, exchanges: list[str] | None = None) -> None:
        client = self._api_client()
        frames: list[pd.DataFrame] = []
        target_exchanges = self._normalize_exchange_filter(exchanges)
        for exchange in target_exchanges:
            logger.info("Refreshing EODHD code_names.csv for %s", exchange)
            frame = client.get_exchange_symbols(uri=exchange, delisted=False)
            if frame.empty:
                logger.warning("EODHD returned no symbols for exchange %s", exchange)
                continue
            normalized = self._normalize_symbol_table(frame, exchange)
            normalized = normalized.drop_duplicates(["symbol"], keep="last").sort_values(
                ["code"]
            )
            self._ensure_exchange_dirs(exchange)
            normalized.to_csv(self._exchange_code_names_path(exchange), index=False)
            self._code_names_by_exchange[exchange] = normalized
            frames.append(normalized)
            logger.info(
                "Wrote EODHD %s/code_names.csv (rows=%d)",
                exchange,
                len(normalized),
            )
        if not frames:
            raise ServiceError(
                f"EODHD returned no symbol tables for exchanges {target_exchanges}"
            )
        self._code_names = None
        self._code_name_index = None

    def _get_exchange_code_names(self, exchange: str) -> pd.DataFrame | None:
        cached = self._code_names_by_exchange.get(exchange)
        if cached is not None:
            return cached
        path = self._exchange_code_names_path(exchange)
        if not path.exists():
            return None
        frame = self._read_csv(path)
        self._code_names_by_exchange[exchange] = frame
        return frame

    @staticmethod
    def _combine_code_name_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
        combined = pd.concat(frames, ignore_index=True, copy=False)
        return combined.drop_duplicates(["symbol"], keep="last").sort_values(
            ["exchange", "code"]
        )

    def _normalize_symbol_table(self, frame: pd.DataFrame, exchange: str) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for row in frame.to_dict(orient="records"):
            code = _text_or_none(self._field(row, ("Code", "code", "Symbol", "symbol")))
            if code is None:
                continue
            symbol = code if "." in code else f"{code}.{exchange}"
            rows.append(
                {
                    "symbol": symbol,
                    "code": code,
                    "exchange": exchange,
                    "name": _text_or_none(self._field(row, ("Name", "name"))) or code,
                    "type": _text_or_none(self._field(row, ("Type", "type"))),
                    "currency": _text_or_none(self._field(row, ("Currency", "currency"))),
                    "isin": _text_or_none(self._field(row, ("Isin", "ISIN", "isin"))),
                    "country": _text_or_none(self._field(row, ("Country", "country"))),
                }
            )
        return pd.DataFrame(
            rows,
            columns=["symbol", "code", "exchange", "name", "type", "currency", "isin", "country"],
        )

    @staticmethod
    def _field(row: dict[str, object], names: tuple[str, ...]) -> object:
        for name in names:
            if name in row:
                return row[name]
        return None

    def subscribe_live_quotes(self, codes: list[str]) -> list[str]:
        if not codes:
            return []
        targets: list[_WebSocketTarget] = []
        supported_codes: list[str] = []
        for code in codes:
            if not self.is_symbol_exchange_enabled(code):
                continue
            target = self._websocket_target_for_code(code)
            if target is None:
                continue
            targets.append(target)
            supported_codes.append(code)
        if targets:
            self._websocket_quotes.subscribe(targets)
        return supported_codes

    def get_snapshots(self, codes: list[str]) -> dict[str, dict[str, object]]:
        supported_codes = self.subscribe_live_quotes(codes)
        if not supported_codes:
            return {}
        snapshots = self._websocket_quotes.wait_for_snapshots(supported_codes)
        out: dict[str, dict[str, object]] = {}
        for code in codes:
            row = snapshots.get(code)
            if row is None:
                continue
            out[code] = {
                "code": code,
                "name": self.get_code_name(code),
                "last_price": row["last_price"],
                "update_time": row["update_time"],
            }
        return out

    def is_websocket_live_quote_supported(self, code: str) -> bool:
        return (
            self.is_symbol_exchange_enabled(code)
            and self._websocket_target_for_code(code) is not None
        )

    @staticmethod
    def _websocket_target_for_code(code: str) -> _WebSocketTarget | None:
        if "." not in code:
            return None
        symbol, exchange = code.rsplit(".", 1)
        symbol = symbol.strip()
        exchange = exchange.strip().upper()
        if not symbol or not exchange:
            return None
        if exchange == "US":
            return _WebSocketTarget(
                endpoint="us",
                wire_symbol=symbol.upper(),
                code=code,
            )
        if exchange == "FOREX":
            return _WebSocketTarget(
                endpoint="forex",
                wire_symbol=symbol.replace("/", "").replace("-", "").upper(),
                code=code,
            )
        if exchange in ("CC", "CRYPTO"):
            return _WebSocketTarget(
                endpoint="crypto",
                wire_symbol=symbol.upper(),
                code=code,
            )
        return None

    @staticmethod
    def _payload_rows(payload: object) -> list[dict[str, object]]:
        if isinstance(payload, pd.DataFrame):
            return payload.to_dict(orient="records")
        if isinstance(payload, list):
            rows: list[dict[str, object]] = []
            for row in payload:
                if isinstance(row, dict):
                    rows.append(dict(row))
            return rows
        if isinstance(payload, dict):
            return [dict(payload)]
        return []

    def fetch_corporate_actions(
        self, ex_date: date, codes: Iterable[str]
    ) -> list[EODHDCorporateAction]:
        """
        Fetch EODHD split/dividend events for held suffixed symbols on ``ex_date``.

        EODHD exposes these through the same bulk endpoint as daily EOD bars,
        but with ``type="splits"`` or ``type="dividends"``. The endpoint is
        exchange/date based, so this method groups held symbols by exchange,
        downloads each exchange's full event set for the date, and filters rows
        down to the held symbols.
        """
        requested_by_exchange: dict[str, dict[str, str]] = {}
        for raw_code in codes:
            if not self.is_symbol_exchange_enabled(raw_code):
                continue
            requested = self._normalize_requested_symbol(raw_code)
            if requested is None:
                logger.warning(
                    "Skipping EODHD corporate-action lookup for unsuffixed symbol %r",
                    raw_code,
                )
                continue
            held_symbol, exchange, lookup_key = requested
            requested_by_exchange.setdefault(exchange, {})[lookup_key] = held_symbol

        if not requested_by_exchange:
            return []

        actions: list[EODHDCorporateAction] = []
        for exchange, requested_symbols in sorted(requested_by_exchange.items()):
            exchange_actions: dict[str, EODHDCorporateAction] = {}
            self._merge_dividend_actions(
                exchange_actions,
                requested_symbols,
                self._fetch_bulk_corporate_action_rows(exchange, ex_date, "dividends"),
                exchange,
                ex_date,
            )
            self._merge_split_actions(
                exchange_actions,
                requested_symbols,
                self._fetch_bulk_corporate_action_rows(exchange, ex_date, "splits"),
                exchange,
                ex_date,
            )
            kept = [
                action
                for action in exchange_actions.values()
                if action.cash_dividend_per_share > 0.0
                or abs(action.split_ratio - 1.0) > 0.000000001
            ]
            actions.extend(kept)
            logger.info(
                "EODHD corporate actions for %s %s: held=%d applicable=%d",
                exchange,
                ex_date,
                len(requested_symbols),
                len(kept),
            )
        return sorted(actions, key=lambda action: action.code)

    def _normalize_requested_symbol(self, raw_code: str) -> tuple[str, str, str] | None:
        symbol = raw_code.strip()
        if "." not in symbol:
            return None
        code, exchange = self._split_symbol(symbol)
        exchange = exchange.upper()
        if not code or not exchange:
            return None
        return symbol, exchange, f"{code}.{exchange}".upper()

    def _fetch_bulk_corporate_action_rows(
        self, exchange: str, ex_date: date, action_type: str
    ) -> list[dict[str, object]]:
        client = self._api_client()
        logger.info(
            "Fetching EODHD %s bulk events for %s %s",
            action_type,
            exchange,
            ex_date,
        )
        payload = client.get_eod_splits_dividends_data(
            country=exchange,
            date=ex_date.isoformat(),
            type=action_type,
        )
        rows = self._payload_rows(payload)
        logger.info(
            "Fetched EODHD %s bulk events for %s %s (rows=%d)",
            action_type,
            exchange,
            ex_date,
            len(rows),
        )
        return rows

    def _merge_dividend_actions(
        self,
        actions: dict[str, EODHDCorporateAction],
        requested_symbols: dict[str, str],
        rows: list[dict[str, object]],
        exchange: str,
        ex_date: date,
    ) -> None:
        for row in rows:
            if not self._corporate_action_row_matches_date(row, ex_date):
                continue
            symbol = self._corporate_action_symbol(row, exchange)
            if symbol is None:
                continue
            requested = requested_symbols.get(symbol.upper())
            if requested is None:
                continue
            amount = self._dividend_amount_from_row(row)
            if amount is None or amount <= 0.0:
                continue
            action = self._corporate_action_for(actions, requested, exchange, ex_date)
            action.cash_dividend_per_share += amount
            currency = _text_or_none(self._field(row, ("currency", "Currency")))
            if currency is not None:
                action.dividend_currency = currency
            period = _text_or_none(self._field(row, ("period", "Period")))
            if period is not None:
                action.dividend_period = period

    def _merge_split_actions(
        self,
        actions: dict[str, EODHDCorporateAction],
        requested_symbols: dict[str, str],
        rows: list[dict[str, object]],
        exchange: str,
        ex_date: date,
    ) -> None:
        for row in rows:
            if not self._corporate_action_row_matches_date(row, ex_date):
                continue
            symbol = self._corporate_action_symbol(row, exchange)
            if symbol is None:
                continue
            requested = requested_symbols.get(symbol.upper())
            if requested is None:
                continue
            split_text = _text_or_none(self._field(row, ("split", "Split", "ratio", "Ratio")))
            ratio = self._split_ratio_from_text(split_text)
            if ratio is None or ratio <= 0.0 or abs(ratio - 1.0) <= 0.000000001:
                continue
            action = self._corporate_action_for(actions, requested, exchange, ex_date)
            action.split_ratio *= ratio
            if split_text is not None:
                if action.split_text:
                    action.split_text = f"{action.split_text}; {split_text}"
                else:
                    action.split_text = split_text

    @staticmethod
    def _corporate_action_for(
        actions: dict[str, EODHDCorporateAction],
        symbol: str,
        exchange: str,
        ex_date: date,
    ) -> EODHDCorporateAction:
        action = actions.get(symbol)
        if action is None:
            action = EODHDCorporateAction(code=symbol, exchange=exchange, ex_date=ex_date)
            actions[symbol] = action
        return action

    def _corporate_action_symbol(
        self, row: dict[str, object], fallback_exchange: str
    ) -> str | None:
        raw_code = _text_or_none(
            self._field(row, ("code", "Code", "symbol", "Symbol", "ticker", "Ticker"))
        )
        if raw_code is None:
            return None
        if "." in raw_code:
            code, exchange = self._split_symbol(raw_code)
            exchange = exchange.upper()
            return f"{code}.{exchange}" if code and exchange else None
        return f"{raw_code}.{fallback_exchange}"

    def _corporate_action_row_matches_date(
        self, row: dict[str, object], ex_date: date
    ) -> bool:
        raw_date = _text_or_none(
            self._field(row, ("date", "Date", "exDate", "ex_date", "exDividendDate"))
        )
        if raw_date is None:
            return True
        try:
            return date.fromisoformat(raw_date[:10]) == ex_date
        except ValueError:
            logger.warning(
                "Could not parse EODHD corporate-action date %r; keeping row",
                raw_date,
            )
            return True

    def _dividend_amount_from_row(self, row: dict[str, object]) -> float | None:
        for name in (
            "unadjustedValue",
            "unadjusted_value",
            "dividend",
            "Dividend",
            "value",
            "Value",
            "amount",
            "Amount",
        ):
            value = _float_or_none(self._field(row, (name,)))
            if value is not None and value > 0.0:
                return value
        return None

    @staticmethod
    def _split_ratio_from_text(value: str | None) -> float | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        for delimiter in (":", "/"):
            if delimiter not in text:
                continue
            parts = text.split(delimiter)
            if len(parts) != 2:
                return None
            numerator = _float_or_none(parts[0].strip())
            denominator = _float_or_none(parts[1].strip())
            if numerator is None or denominator is None or denominator == 0.0:
                return None
            return numerator / denominator
        return _float_or_none(text)

    def get_latest_daily_bar(self) -> pd.DataFrame | None:
        if self._latest_daily_frame is not None:
            return self._latest_daily_frame
        latest_day: str | None = None
        for exchange in self.exchanges:
            daily_dir = self._daily_dir(exchange)
            if not daily_dir.exists():
                continue
            for path in daily_dir.glob("*.csv"):
                if latest_day is None or path.stem > latest_day:
                    latest_day = path.stem
        if latest_day is None:
            return None

        frames: list[pd.DataFrame] = []
        for exchange in self.exchanges:
            path = self._daily_dir(exchange) / f"{latest_day}.csv"
            if not path.exists():
                continue
            frame = self._read_csv(path)
            if not frame.empty:
                frames.append(frame)
        if frames:
            self._latest_daily_frame = pd.concat(frames, ignore_index=True, copy=False)
            self._latest_daily_frame = self._latest_daily_frame.sort_values(["exchange", "code"])
            return self._latest_daily_frame
        return None

    def persist_history(
        self,
        start_date: date,
        end_date: date,
        bars: str = "both",
        overwrite: bool = False,
        persist_every: int = 100,
        show_progress: bool = False,
        verbose: bool = False,
        exchanges: list[str] | None = None,
    ) -> int:
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        if persist_every <= 0:
            raise ValueError("persist_every must be positive")
        want_daily = bars in ("daily", "both")
        want_5min = bars in ("5min", "both")
        if not want_daily and not want_5min:
            raise ValueError("bars must be daily, 5min, or both")

        total_rows = 0
        if want_daily:
            total_rows += self.persist_daily_history(
                start_date,
                end_date,
                overwrite=overwrite,
                show_progress=show_progress,
                verbose=verbose,
                exchanges=exchanges,
            )
        if want_5min:
            total_rows += self.persist_intraday_history(
                start_date,
                end_date,
                overwrite=overwrite,
                persist_every=persist_every,
                show_progress=show_progress,
                verbose=verbose,
                exchanges=exchanges,
            )
        return total_rows

    def persist_daily_history(
        self,
        start_date: date,
        end_date: date,
        *,
        overwrite: bool = False,
        show_progress: bool = False,
        verbose: bool = False,
        exchanges: list[str] | None = None,
    ) -> int:
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        target_exchanges = self._normalize_exchange_filter(exchanges)
        dates_by_exchange = {
            exchange: self.get_trading_dates(exchange, start_date, end_date)
            for exchange in target_exchanges
        }
        exchange_date_count = sum(len(dates) for dates in dates_by_exchange.values())
        total_rows = 0
        logger.info(
            "Persisting EODHD daily bulk bars for %d exchanges and %d exchange-dates",
            len(target_exchanges),
            exchange_date_count,
        )
        for exchange in target_exchanges:
            self._ensure_exchange_dirs(exchange)
            for day in dates_by_exchange[exchange]:
                path = self._daily_path(exchange, day)
                if not overwrite and path.exists():
                    if verbose:
                        logger.info(
                            "Skipping existing EODHD daily bars for %s %s",
                            exchange,
                            day,
                        )
                    continue
                if show_progress:
                    logger.info("Fetching EODHD daily bulk bars for %s %s", exchange, day)
                frame = self._fetch_bulk_daily(day, exchange)
                if frame.empty:
                    logger.info("EODHD daily bulk returned no rows for %s %s", exchange, day)
                    continue
                self._persist_daily_frame(frame, exchange, day)
                total_rows += len(frame)
                logger.info(
                    "Persisted EODHD daily bars for %s %s (rows=%d)",
                    exchange,
                    day,
                    len(frame),
                )
        self._latest_daily_frame = None
        return total_rows

    def persist_intraday_history(
        self,
        start_date: date,
        end_date: date,
        *,
        overwrite: bool = False,
        persist_every: int = 100,
        show_progress: bool = False,
        verbose: bool = False,
        exchanges: list[str] | None = None,
    ) -> int:
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        if persist_every <= 0:
            raise ValueError("persist_every must be positive")
        target_exchanges = self._normalize_exchange_filter(exchanges)
        total_rows = 0
        logger.info(
            "Persisting EODHD 5min intraday bars for %d exchanges",
            len(target_exchanges),
        )
        for exchange in target_exchanges:
            self._ensure_exchange_dirs(exchange)
            frame = self._get_exchange_code_names(exchange)
            if frame is None or frame.empty:
                self.refresh_code_names([exchange])
                frame = self._get_exchange_code_names(exchange)
            if frame is None or frame.empty:
                raise ServiceError(f"No EODHD symbols available for exchange {exchange}")
            symbols = frame["symbol"].astype(str).tolist()
            logger.info(
                "Persisting EODHD 5min intraday bars for %s by symbol (symbols=%d)",
                exchange,
                len(symbols),
            )
            total_rows += self._persist_intraday_for_symbols(
                exchange,
                symbols,
                start_date,
                end_date,
                overwrite=overwrite,
                persist_every=persist_every,
                show_progress=show_progress,
                verbose=verbose,
            )
        return total_rows

    def _normalize_exchange_filter(self, exchanges: list[str] | None) -> list[str]:
        if exchanges is None:
            return list(self.exchanges)
        normalized: list[str] = []
        seen: set[str] = set()
        for exchange in exchanges:
            value = exchange.strip().upper()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        if not normalized:
            raise ValueError("At least one EODHD exchange must be selected")
        return normalized

    def get_trading_dates(
        self,
        exchange: str,
        start_date: date,
        end_date: date,
    ) -> list[date]:
        exchange = exchange.strip().upper()
        payload = self._api_client().get_details_trading_hours_stock_market_holidays(
            code=exchange,
            from_date=start_date.isoformat(),
            to_date=end_date.isoformat(),
        )
        weekday_numbers = {
            "mon": 0,
            "tue": 1,
            "wed": 2,
            "thu": 3,
            "fri": 4,
            "sat": 5,
            "sun": 6,
        }
        working_days = {
            weekday_numbers[value.strip().lower()[:3]]
            for value in payload["TradingHours"]["WorkingDays"].split(",")
        }
        closed_dates = {
            date.fromisoformat(holiday["Date"])
            for holiday in payload["ExchangeHolidays"].values()
            if holiday["Type"].lower().replace("-", "").replace(" ", "")
            != "earlyclose"
        }

        trading_dates: list[date] = []
        cursor = start_date
        while cursor <= end_date:
            if cursor.weekday() in working_days and cursor not in closed_dates:
                trading_dates.append(cursor)
            cursor += timedelta(days=1)
        return trading_dates

    def is_trading_day(self, exchange: str, day: date) -> bool:
        return day in self.get_trading_dates(exchange, day, day)

    @staticmethod
    def _business_dates(start_date: date, end_date: date) -> list[date]:
        dates: list[date] = []
        cursor = start_date
        while cursor <= end_date:
            if cursor.weekday() < 5:
                dates.append(cursor)
            cursor += timedelta(days=1)
        return dates

    def _fetch_bulk_daily(self, day: date, exchange: str) -> pd.DataFrame:
        client = self._api_client()
        rows: list[dict[str, object]] = []
        payload = client.get_eod_splits_dividends_data(country=exchange, date=day.isoformat())
        for row in self._payload_rows(payload):
            normalized = self._normalize_daily_row(row, exchange, day)
            if normalized is not None:
                rows.append(normalized)
        return pd.DataFrame(
            rows,
            columns=[
                "date",
                "symbol",
                "code",
                "exchange",
                "open",
                "high",
                "low",
                "close",
                "adjusted_close",
                "volume",
            ],
        )

    def _normalize_daily_row(
        self, row: dict[str, object], exchange: str, fallback_date: date
    ) -> dict[str, object] | None:
        code = _text_or_none(self._field(row, ("code", "Code", "symbol", "Symbol")))
        if code is None:
            return None
        symbol = code if "." in code else f"{code}.{exchange}"
        close = _float_or_none(self._field(row, ("close", "Close")))
        if close is None:
            return None
        return {
            "date": _text_or_none(self._field(row, ("date", "Date"))) or fallback_date.isoformat(),
            "symbol": symbol,
            "code": symbol.rsplit(".", 1)[0],
            "exchange": exchange,
            "open": _float_or_none(self._field(row, ("open", "Open"))),
            "high": _float_or_none(self._field(row, ("high", "High"))),
            "low": _float_or_none(self._field(row, ("low", "Low"))),
            "close": close,
            "adjusted_close": _float_or_none(
                self._field(row, ("adjusted_close", "adjustedClose"))
            ),
            "volume": _int_or_none(self._field(row, ("volume", "Volume"))),
        }

    def _persist_intraday_for_symbols(
        self,
        exchange: str,
        symbols: list[str],
        start_date: date,
        end_date: date,
        *,
        overwrite: bool,
        persist_every: int,
        show_progress: bool,
        verbose: bool,
    ) -> int:
        total_rows = 0
        buffers: list[pd.DataFrame] = []
        existing = (
            self._existing_intraday_symbols(exchange, start_date, end_date)
            if not overwrite
            else {}
        )
        if show_progress:
            logger.info("Fetching EODHD 5min bars for %s (%d symbols)", exchange, len(symbols))
        for index, symbol in enumerate(symbols, start=1):
            if not overwrite and self._symbol_has_all_dates(symbol, start_date, end_date, existing):
                if verbose:
                    logger.info("Skipping existing EODHD 5min bars for %s", symbol)
                continue
            frame = self._fetch_five_minute_bars(symbol, start_date, end_date)
            if not frame.empty:
                buffers.append(frame)
            if verbose:
                logger.info("Fetched EODHD 5min bars for %s (rows=%d)", symbol, len(frame))
            if index % persist_every == 0 and buffers:
                combined = pd.concat(buffers, ignore_index=True, copy=False)
                self._persist_five_minute_frame(combined)
                total_rows += len(combined)
                buffers.clear()
                if show_progress:
                    logger.info(
                        "Persisted EODHD 5min bars for %s through symbol %d/%d",
                        exchange,
                        index,
                        len(symbols),
                    )
        if buffers:
            combined = pd.concat(buffers, ignore_index=True, copy=False)
            self._persist_five_minute_frame(combined)
            total_rows += len(combined)
        return total_rows

    def _existing_intraday_symbols(
        self, exchange: str, start_date: date, end_date: date
    ) -> dict[str, set[str]]:
        existing: dict[str, set[str]] = {}
        for day in self._business_dates(start_date, end_date):
            path = self._five_min_path(exchange, day)
            if not path.exists():
                existing[day.isoformat()] = set()
                continue
            frame = self._read_csv(path)
            existing[day.isoformat()] = set() if frame.empty else set(frame["symbol"].astype(str))
        return existing

    def _symbol_has_all_dates(
        self, symbol: str, start_date: date, end_date: date, existing: dict[str, set[str]]
    ) -> bool:
        return all(
            symbol in existing.get(day.isoformat(), set())
            for day in self._business_dates(start_date, end_date)
        )

    def _fetch_five_minute_bars(
        self, symbol: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        client = self._api_client()
        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        payload = client.get_intraday_historical_data(
            symbol=symbol,
            interval="5m",
            from_unix_time=str(int(start_dt.timestamp())),
            to_unix_time=str(int(end_dt.timestamp())),
        )
        rows: list[dict[str, object]] = []
        code, exchange = self._split_symbol(symbol)
        for row in self._payload_rows(payload):
            normalized = self._normalize_intraday_row(row, symbol, code, exchange)
            if normalized is not None:
                rows.append(normalized)
        return self._intraday_frame(rows)

    def fetch_intraday_window(
        self, symbol: str, start_dt_utc: datetime, end_dt_utc: datetime
    ) -> pd.DataFrame:
        if start_dt_utc.tzinfo is None:
            start_dt_utc = start_dt_utc.replace(tzinfo=timezone.utc)
        if end_dt_utc.tzinfo is None:
            end_dt_utc = end_dt_utc.replace(tzinfo=timezone.utc)
        start_dt_utc = start_dt_utc.astimezone(timezone.utc)
        end_dt_utc = end_dt_utc.astimezone(timezone.utc)
        if end_dt_utc <= start_dt_utc:
            raise ValueError("end_dt_utc must be after start_dt_utc")

        client = self._api_client()
        payload = client.get_intraday_historical_data(
            symbol=symbol,
            interval="5m",
            from_unix_time=str(int(start_dt_utc.timestamp())),
            to_unix_time=str(int(end_dt_utc.timestamp())),
        )
        code, exchange = self._split_symbol(symbol)
        rows: list[dict[str, object]] = []
        for row in self._payload_rows(payload):
            normalized = self._normalize_intraday_row(row, symbol, code, exchange)
            if normalized is not None:
                rows.append(normalized)
        frame = self._intraday_frame(rows)
        if frame.empty:
            return frame
        start_ts = int(start_dt_utc.timestamp())
        end_ts = int(end_dt_utc.timestamp())
        filtered = frame[
            (frame["timestamp"].astype(int) >= start_ts)
            & (frame["timestamp"].astype(int) < end_ts)
        ]
        return filtered.sort_values("timestamp").reset_index(drop=True)

    @staticmethod
    def _intraday_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=_INTRADAY_COLUMNS)
        return frame.astype(_INTRADAY_DTYPES)

    def _normalize_intraday_row(
        self, row: dict[str, object], symbol: str, code: str, exchange: str
    ) -> dict[str, object] | None:
        timestamp = _int_or_none(self._field(row, ("timestamp", "epoch")))
        raw_datetime = _text_or_none(self._field(row, ("datetime", "date")))
        if timestamp is None and raw_datetime is None:
            return None
        if timestamp is not None:
            dt = datetime.fromtimestamp(timestamp, timezone.utc)
        else:
            dt = datetime.fromisoformat(str(raw_datetime).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            timestamp = int(dt.timestamp())
        close = _float_or_none(self._field(row, ("close", "Close")))
        if close is None:
            return None
        return {
            "date": dt.date().isoformat(),
            "datetime_utc": dt.replace(tzinfo=timezone.utc).isoformat(),
            "timestamp": timestamp,
            "gmtoffset": _int_or_none(self._field(row, ("gmtoffset",))),
            "symbol": symbol,
            "code": code,
            "exchange": exchange,
            "open": _float_or_none(self._field(row, ("open", "Open"))),
            "high": _float_or_none(self._field(row, ("high", "High"))),
            "low": _float_or_none(self._field(row, ("low", "Low"))),
            "close": close,
            "volume": _int_or_none(self._field(row, ("volume", "Volume"))),
        }

    @staticmethod
    def _split_symbol(symbol: str) -> tuple[str, str]:
        if "." not in symbol:
            return symbol, ""
        code, exchange = symbol.rsplit(".", 1)
        return code, exchange

    def _persist_daily_frame(self, frame: pd.DataFrame, exchange: str, day: date) -> None:
        if frame.empty:
            return
        cleaned = frame.drop_duplicates(["symbol"], keep="last").sort_values(["code"])
        self._write_frame(self._daily_path(exchange, day), cleaned)

    def _persist_five_minute_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        for (exchange, day_iso), sub in frame.groupby(["exchange", "date"], sort=False):
            day = date.fromisoformat(str(day_iso))
            self._merge_and_write(
                self._five_min_path(str(exchange), day),
                sub,
                ["symbol", "timestamp"],
                sort_keys=["timestamp", "symbol"],
            )

    @staticmethod
    def _write_frame(path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)

    def _merge_and_write(
        self,
        path: Path,
        new_rows: pd.DataFrame,
        dedupe_keys: list[str],
        sort_keys: list[str] | None = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = (
            pd.concat([self._read_csv(path), new_rows], ignore_index=True, copy=False)
            if path.exists()
            else new_rows
        )
        merged = merged.drop_duplicates(dedupe_keys, keep="last").sort_values(
            sort_keys or dedupe_keys
        )
        merged.to_csv(path, index=False)

    async def run(self) -> None:
        last_refreshed_date: date | None = None
        last_finalized_daily: set[tuple[str, date]] = set()
        last_finalized_5min: set[tuple[str, date]] = set()
        while True:
            now = datetime.now(timezone.utc)
            today = now.date()
            retry_finalization = False
            if last_refreshed_date != today:
                last_refreshed_date = today
                try:
                    await asyncio.to_thread(self.refresh_code_names)
                except Exception:
                    logger.exception("Failed to refresh EODHD code_names.csv")
            for exchange, exchange_config in self.exchange_configs.items():
                target = today + timedelta(
                    days=exchange_config.target_date_offset_days
                )
                key = (exchange, target)
                if (
                    exchange_config.daily_bars.enabled
                    and now.time() >= exchange_config.daily_bars.finalize_time_utc
                    and key not in last_finalized_daily
                ):
                    try:
                        logger.info(
                            "Starting EODHD daily finalization for %s %s",
                            exchange,
                            target,
                        )
                        rows = await asyncio.to_thread(
                            self.persist_daily_history,
                            target,
                            target,
                            overwrite=False,
                            show_progress=False,
                            verbose=False,
                            exchanges=[exchange],
                        )
                        last_finalized_daily.add(key)
                        logger.info(
                            "Finalized EODHD daily bars for %s %s (rows=%d)",
                            exchange,
                            target,
                            rows,
                        )
                    except Exception:
                        retry_finalization = True
                        logger.exception(
                            "Exception finalizing EODHD daily bars for %s %s",
                            exchange,
                            target,
                        )
                if (
                    exchange_config.five_min_bars.enabled
                    and now.time() >= exchange_config.five_min_bars.finalize_time_utc
                    and key not in last_finalized_5min
                ):
                    try:
                        logger.info(
                            "Starting EODHD 5min finalization for %s %s",
                            exchange,
                            target,
                        )
                        rows = await asyncio.to_thread(
                            self.persist_intraday_history,
                            target,
                            target,
                            overwrite=False,
                            persist_every=100,
                            show_progress=False,
                            verbose=False,
                            exchanges=[exchange],
                        )
                        last_finalized_5min.add(key)
                        logger.info(
                            "Finalized EODHD 5min bars for %s %s (rows=%d)",
                            exchange,
                            target,
                            rows,
                        )
                    except Exception:
                        retry_finalization = True
                        logger.exception(
                            "Exception finalizing EODHD 5min bars for %s %s",
                            exchange,
                            target,
                        )
            wake_times = [
                datetime.combine(
                    today + timedelta(days=1),
                    time.min,
                    tzinfo=timezone.utc,
                )
            ]
            for exchange_config in self.exchange_configs.values():
                for schedule in (
                    exchange_config.daily_bars,
                    exchange_config.five_min_bars,
                ):
                    if not schedule.enabled:
                        continue
                    scheduled_at = datetime.combine(
                        today,
                        schedule.finalize_time_utc,
                        tzinfo=timezone.utc,
                    )
                    if scheduled_at <= now:
                        scheduled_at += timedelta(days=1)
                    wake_times.append(scheduled_at)
            if retry_finalization:
                wake_times.append(datetime.now(timezone.utc) + timedelta(minutes=1))
            sleep_seconds = (min(wake_times) - datetime.now(timezone.utc)).total_seconds()
            await asyncio.sleep(max(1.0, sleep_seconds))

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        return pd.read_csv(path, dtype={"symbol": str, "code": str, "exchange": str})
