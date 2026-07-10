"""EODHD-backed market-data service and live quote adapter."""

import asyncio
import shutil
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from importlib import resources
from importlib.metadata import PackageNotFoundError, version
from logging import getLogger
from pathlib import Path

import pandas as pd

from quant_arena.config import EODHDMarketScheduleConfig
from quant_arena.errors import ServiceError

logger = getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class _RuntimeMarketSchedule:
    exchange: str
    daily_finalize_time_utc: time
    five_min_finalize_time_utc: time
    target_date_offset_days: int


class EODHDService:
    """EODHD all-in-one data persistence plus live quote snapshots."""

    def __init__(
        self,
        *,
        api_token: str,
        market_data_root: Path,
        market_schedules: list[EODHDMarketScheduleConfig],
    ):
        self.api_token = api_token
        self.market_data_root = market_data_root
        self.market_bars_dir = market_data_root / "bars"
        self.market_schedules = self._normalize_market_schedules(market_schedules)
        self.exchanges = [schedule.exchange for schedule in self.market_schedules]
        self._code_names_path = market_data_root / "code_names.csv"
        self._code_names: pd.DataFrame | None = None
        self._code_name_index: dict[str, str] | None = None
        self._latest_daily_frame: pd.DataFrame | None = None
        self._client = None  # eodhd.APIClient, created lazily
        self.market_bars_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            resources.files("quant_arena.resources").joinpath("README-eodhd-market-data.md"),
            market_data_root / "README.md",
        )

    @staticmethod
    def _normalize_market_schedules(
        schedules: list[EODHDMarketScheduleConfig],
    ) -> list[_RuntimeMarketSchedule]:
        normalized: list[_RuntimeMarketSchedule] = []
        seen: set[str] = set()
        for schedule in schedules:
            if not schedule.enabled:
                continue
            exchange = schedule.exchange.strip().upper()
            if not exchange or exchange in seen:
                continue
            seen.add(exchange)
            normalized.append(
                _RuntimeMarketSchedule(
                    exchange=exchange,
                    daily_finalize_time_utc=_utc_time_from_text(schedule.daily_finalize_utc),
                    five_min_finalize_time_utc=_utc_time_from_text(schedule.five_min_finalize_utc),
                    target_date_offset_days=schedule.target_date_offset_days,
                )
            )
        if not normalized:
            raise ValueError("At least one EODHD market schedule must be enabled")
        return normalized

    def _api_client(self):
        if self._client is None:
            from eodhd import APIClient

            self._client = APIClient(self.api_token)
        return self._client

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
        if self._code_names is None and self._code_names_path.exists():
            self._code_names = self._read_csv(self._code_names_path)
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

    def refresh_code_names(self) -> None:
        client = self._api_client()
        frames: list[pd.DataFrame] = []
        for exchange in self.exchanges:
            frame = client.get_exchange_symbols(uri=exchange, delisted=False)
            if frame.empty:
                logger.warning("EODHD returned no symbols for exchange %s", exchange)
                continue
            frames.append(self._normalize_symbol_table(frame, exchange))
        if not frames:
            raise ServiceError("EODHD returned no symbol tables for configured exchanges")
        combined = pd.concat(frames, ignore_index=True, copy=False)
        combined = combined.drop_duplicates(["symbol"], keep="last").sort_values(
            ["exchange", "code"]
        )
        self._code_names_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(self._code_names_path, index=False)
        self._code_names = combined
        self._code_name_index = None

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

    def get_snapshots(self, codes: list[str]) -> dict[str, dict[str, object]]:
        if not codes:
            return {}
        client = self._api_client()
        first = codes[0]
        rest = ",".join(codes[1:]) if len(codes) > 1 else None
        payload = client.get_live_stock_prices(ticker=first, s=rest)
        rows = self._payload_rows(payload)
        out: dict[str, dict[str, object]] = {}
        for row in rows:
            symbol = _text_or_none(self._field(row, ("code", "symbol", "ticker")))
            if symbol is None:
                symbol = first if len(codes) == 1 else None
            if symbol is None:
                continue
            if "." not in symbol:
                match = self._match_symbol(symbol, codes)
                if match is None:
                    continue
                symbol = match
            price = self._price_from_row(row)
            if price is None or price <= 0:
                continue
            timestamp = _int_or_none(self._field(row, ("timestamp", "date", "time")))
            update_time = None
            if timestamp is not None:
                update_time = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
            name = self.get_code_name(symbol)
            out[symbol] = {
                "code": symbol,
                "name": name,
                "last_price": price,
                "update_time": update_time,
            }
        return out

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

    @staticmethod
    def _match_symbol(code: str, candidates: list[str]) -> str | None:
        for candidate in candidates:
            if candidate == code or candidate.rsplit(".", 1)[0] == code:
                return candidate
        return None

    def _price_from_row(self, row: dict[str, object]) -> float | None:
        for name in ("close", "price", "last", "adjusted_close"):
            value = _float_or_none(self._field(row, (name,)))
            if value is not None and value > 0:
                return value
        return None

    def get_latest_daily_bar(self) -> pd.DataFrame | None:
        if self._latest_daily_frame is not None:
            return self._latest_daily_frame
        for day_dir in sorted(
            (path for path in self.market_bars_dir.iterdir() if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        ):
            path = day_dir / "daily.csv"
            if path.exists():
                frame = self._read_csv(path)
                if not frame.empty:
                    self._latest_daily_frame = frame
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

        target_exchanges = self._normalize_exchange_filter(exchanges)
        dates = self._business_dates(start_date, end_date)
        total_rows = 0
        if want_daily:
            for day in dates:
                if not overwrite and (self.market_bars_dir / day.isoformat() / "daily.csv").exists():
                    existing = self._read_csv(self.market_bars_dir / day.isoformat() / "daily.csv")
                    existing_exchanges = set(existing["exchange"].astype(str)) if not existing.empty else set()
                    if all(exchange in existing_exchanges for exchange in target_exchanges):
                        continue
                frame = self._fetch_bulk_daily(day, target_exchanges)
                if not frame.empty:
                    self._persist_daily_frame(frame)
                    total_rows += len(frame)
            self._latest_daily_frame = None
        if want_5min:
            frame = self.get_code_names()
            if frame is None or frame.empty:
                self.refresh_code_names()
                frame = self.get_code_names()
            if frame is None or frame.empty:
                raise ServiceError("No EODHD symbols available for 5-minute persistence")
            frame = frame[frame["exchange"].astype(str).isin(target_exchanges)]
            if frame.empty:
                raise ServiceError(
                    f"No EODHD symbols available for exchanges {target_exchanges}"
                )
            total_rows += self._persist_intraday_for_symbols(
                frame["symbol"].astype(str).tolist(),
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

    @staticmethod
    def _business_dates(start_date: date, end_date: date) -> list[date]:
        dates: list[date] = []
        cursor = start_date
        while cursor <= end_date:
            if cursor.weekday() < 5:
                dates.append(cursor)
            cursor += timedelta(days=1)
        return dates

    def _fetch_bulk_daily(self, day: date, exchanges: list[str]) -> pd.DataFrame:
        client = self._api_client()
        rows: list[dict[str, object]] = []
        for exchange in exchanges:
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
        existing = self._existing_intraday_symbols(start_date, end_date) if not overwrite else {}
        if show_progress:
            logger.info("Fetching EODHD 5min bars for %d symbols", len(symbols))
        for index, symbol in enumerate(symbols, start=1):
            if not overwrite and self._symbol_has_all_dates(symbol, start_date, end_date, existing):
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
                    logger.info("Persisted EODHD 5min bars through symbol %d/%d", index, len(symbols))
        if buffers:
            combined = pd.concat(buffers, ignore_index=True, copy=False)
            self._persist_five_minute_frame(combined)
            total_rows += len(combined)
        return total_rows

    def _existing_intraday_symbols(self, start_date: date, end_date: date) -> dict[str, set[str]]:
        existing: dict[str, set[str]] = {}
        for day in self._business_dates(start_date, end_date):
            path = self.market_bars_dir / day.isoformat() / "5min.csv"
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

    def _fetch_five_minute_bars(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
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
        return pd.DataFrame(
            rows,
            columns=[
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
            ],
        )

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
        frame = pd.DataFrame(
            rows,
            columns=[
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
            ],
        )
        if frame.empty:
            return frame
        start_ts = int(start_dt_utc.timestamp())
        end_ts = int(end_dt_utc.timestamp())
        filtered = frame[
            (frame["timestamp"].astype(int) >= start_ts)
            & (frame["timestamp"].astype(int) < end_ts)
        ]
        return filtered.sort_values("timestamp").reset_index(drop=True)

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

    def _persist_daily_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        for day_iso, sub in frame.groupby("date", sort=False):
            self._merge_and_write(
                self.market_bars_dir / str(day_iso) / "daily.csv",
                sub,
                ["symbol"],
                sort_keys=["exchange", "code"],
            )

    def _persist_five_minute_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        for day_iso, sub in frame.groupby("date", sort=False):
            self._merge_and_write(
                self.market_bars_dir / str(day_iso) / "5min.csv",
                sub,
                ["symbol", "timestamp"],
                sort_keys=["timestamp", "symbol"],
            )

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

    async def run(self, polling_interval_seconds: int) -> None:
        last_refreshed_date: date | None = None
        last_finalized_daily: set[tuple[str, date]] = set()
        last_finalized_5min: set[tuple[str, date]] = set()
        while True:
            now = datetime.now(timezone.utc)
            today = now.date()
            if last_refreshed_date != today:
                last_refreshed_date = today
                try:
                    await asyncio.to_thread(self.refresh_code_names)
                except Exception:
                    logger.exception("Failed to refresh EODHD code_names.csv")
            for schedule in self.market_schedules:
                target = today + timedelta(days=schedule.target_date_offset_days)
                key = (schedule.exchange, target)
                if (
                    now.time() >= schedule.daily_finalize_time_utc
                    and key not in last_finalized_daily
                ):
                    try:
                        logger.info(
                            "Starting EODHD daily finalization for %s %s",
                            schedule.exchange,
                            target,
                        )
                        rows = await asyncio.to_thread(
                            self.persist_history,
                            target,
                            target,
                            "daily",
                            True,
                            500,
                            False,
                            False,
                            [schedule.exchange],
                        )
                        last_finalized_daily.add(key)
                        logger.info(
                            "Finalized EODHD daily bars for %s %s (rows=%d)",
                            schedule.exchange,
                            target,
                            rows,
                        )
                    except Exception:
                        logger.exception(
                            "Exception finalizing EODHD daily bars for %s %s",
                            schedule.exchange,
                            target,
                        )
                if (
                    now.time() >= schedule.five_min_finalize_time_utc
                    and key not in last_finalized_5min
                ):
                    try:
                        logger.info(
                            "Starting EODHD 5min finalization for %s %s",
                            schedule.exchange,
                            target,
                        )
                        rows = await asyncio.to_thread(
                            self.persist_history,
                            target,
                            target,
                            "5min",
                            True,
                            100,
                            False,
                            False,
                            [schedule.exchange],
                        )
                        last_finalized_5min.add(key)
                        logger.info(
                            "Finalized EODHD 5min bars for %s %s (rows=%d)",
                            schedule.exchange,
                            target,
                            rows,
                        )
                    except Exception:
                        logger.exception(
                            "Exception finalizing EODHD 5min bars for %s %s",
                            schedule.exchange,
                            target,
                        )
            await asyncio.sleep(polling_interval_seconds)

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        return pd.read_csv(path, dtype={"symbol": str, "code": str, "exchange": str})
