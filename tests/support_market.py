import asyncio
from datetime import date, datetime, time, timezone

from quant_arena.clock import SHANGHAI_TZ
from quant_arena.config import AppConfig
from quant_arena.errors import NotFoundError
from quant_arena.models import CodeNameEntry, DailyBar, DataParserJobConfig, DataParserJobEntry, FiveMinuteBar
from quant_arena.schemas import CodeRefreshResponse, CodeSearchResponse, MarketBarsResponse, MarketCodeStatus, MarketParseResponse, MarketStatusResponse
from quant_arena.storage import StorageService


class StaticMarketService:
    """In-memory market service used by tests."""

    def __init__(
        self,
        config: AppConfig,
        storage: StorageService,
        code_names: list[CodeNameEntry] | None = None,
        daily_bars: dict[tuple[str, date], DailyBar] | None = None,
        five_minute_bars: dict[tuple[str, date], list[FiveMinuteBar]] | None = None,
    ):
        self.config = config
        self.storage = storage
        self._code_names = code_names or []
        self._daily_bars = daily_bars or {}
        self._five_minute_bars = five_minute_bars or {}
        self._jobs: list[DataParserJobEntry] = []

    def get_code_names_mapping(self) -> dict[str, str] | None:
        return {entry.code: entry.name for entry in self._code_names}

    def refresh_code_names(self) -> None:
        self.storage.save_code_names(self._code_names)

    def get_daily_bars(self, codes: list[str], trade_date: date) -> dict[str, DailyBar | None]:
        return {
            code: self._daily_bars[(code, trade_date)] if (code, trade_date) in self._daily_bars else None
            for code in codes
        }

    def get_five_minute_bars(self, codes: list[str], trade_date: date) -> dict[str, list[FiveMinuteBar] | None]:
        return {
            code: self._five_minute_bars[(code, trade_date)] if (code, trade_date) in self._five_minute_bars else None
            for code in codes
        }

    def get_latest_prices(self, codes: list[str]) -> dict[str, float | None]:
        prices: dict[str, float | None] = {}
        for code in codes:
            latest_five = None
            for (bar_code, trade_date), bars in self._five_minute_bars.items():
                if bar_code != code or not bars:
                    continue
                candidate = bars[-1]
                if latest_five is None or candidate.bar_time > latest_five.bar_time:
                    latest_five = candidate
            if latest_five is not None:
                prices[code] = latest_five.close_price
                continue
            latest_daily = None
            for (bar_code, _trade_date), bar in self._daily_bars.items():
                if bar_code != code:
                    continue
                if latest_daily is None or bar.trade_date > latest_daily.trade_date:
                    latest_daily = bar
            prices[code] = latest_daily.close_price if latest_daily is not None else None
        return prices

    def create_data_parser_job(self, config: DataParserJobConfig) -> DataParserJobEntry:
        job = DataParserJobEntry(
            config=config,
            skipped=0 if config.skip_existing else None,
            parsed=0,
            error=None,
            start_time=datetime.now(timezone.utc),
            finish_time=None,
        )
        self._jobs.insert(0, job)
        for code in sorted({entry.code for entry in self._code_names}):
            stored_daily = self._stored_daily_dates(code, config.start_date, config.end_date)
            stored_five = self._stored_five_minute_dates(code, config.start_date, config.end_date)
            available_daily = {
                trade_date
                for (bar_code, trade_date) in self._daily_bars
                if bar_code == code and config.start_date <= trade_date <= config.end_date
            }
            available_five = {
                trade_date
                for (bar_code, trade_date) in self._five_minute_bars
                if bar_code == code and config.start_date <= trade_date <= config.end_date
            }
            needs_daily = config.mode in {"daily", "both"} and (not config.skip_existing or stored_daily != available_daily)
            needs_five = config.mode in {"five_minute", "both"} and (not config.skip_existing or stored_five != available_five)
            if not needs_daily and not needs_five:
                if job.skipped is not None:
                    job.skipped += 1
                continue
            if needs_daily:
                self.storage.save_daily_bar_rows(
                    [bar for (bar_code, trade_date), bar in self._daily_bars.items() if bar_code == code and config.start_date <= trade_date <= config.end_date]
                )
            if needs_five:
                rows: list[FiveMinuteBar] = []
                for (bar_code, trade_date), bars in self._five_minute_bars.items():
                    if bar_code == code and config.start_date <= trade_date <= config.end_date:
                        rows.extend(bars)
                self.storage.save_five_minute_bar_rows(rows)
            job.parsed += 1
        job.finish_time = datetime.now(timezone.utc)
        return job

    def list_data_parser_jobs(self) -> list[DataParserJobEntry]:
        return [job.model_copy() for job in self._jobs]

    def refresh_code_names_if_needed(self, now: datetime | None = None) -> CodeRefreshResponse | None:
        if not self.config.enable_code_name_refresh:
            return None
        return self.refresh_code_names_status(force=False, now=now)

    def refresh_code_names_status(self, force: bool = False, now: datetime | None = None) -> CodeRefreshResponse:
        self.refresh_code_names()
        refreshed_at = self.storage.code_names_last_refreshed_at() or datetime.now(timezone.utc)
        return CodeRefreshResponse(refreshed_at=refreshed_at, entry_count=len(self._code_names))

    def search_code_names(self, query: str = "", page: int = 1, page_size: int = 20) -> CodeSearchResponse:
        items = list(self._code_names)
        needle = query.strip().lower()
        if needle:
            items = [item for item in items if needle in item.code.lower() or needle in item.name.lower()]
        total = len(items)
        start = (max(page, 1) - 1) * page_size
        end = start + page_size
        return CodeSearchResponse(
            query=query,
            page=max(page, 1),
            page_size=page_size,
            total=total,
            items=items[start:end],
            last_refreshed_at=self.storage.code_names_last_refreshed_at(),
            auto_refresh_enabled=self.config.enable_code_name_refresh,
        )

    def sync_market_data(self, tracked_codes: set[str], now: datetime | None = None) -> None:
        return None

    def parse_today_market_data_if_missing(self, tracked_codes: set[str], now: datetime | None = None) -> MarketParseResponse:
        timestamp = now or datetime.now(tz=SHANGHAI_TZ)
        local_today = timestamp.astimezone(SHANGHAI_TZ).date()
        stored_daily = {bar.code for bar in (self.storage.load_daily_bar(local_today) or [])}
        stored_five = {bar.code for bar in (self.storage.load_five_minute_bars(local_today) or [])}
        missing_daily = [code for code in sorted(tracked_codes) if code not in stored_daily]
        missing_five = [code for code in sorted(tracked_codes) if code not in stored_five]
        if missing_daily:
            self.storage.save_daily_bar_rows(
                [
                    bar
                    for (bar_code, trade_date), bar in self._daily_bars.items()
                    if bar_code in missing_daily and trade_date == local_today
                ]
            )
        if missing_five:
            rows: list[FiveMinuteBar] = []
            for (bar_code, trade_date), bars in self._five_minute_bars.items():
                if bar_code in missing_five and trade_date == local_today:
                    rows.extend(bars)
            self.storage.save_five_minute_bar_rows(rows)
        return MarketParseResponse(
            trade_date=local_today,
            tracked_codes=sorted(tracked_codes),
            parsed_daily_codes=missing_daily,
            parsed_five_minute_codes=missing_five,
        )

    async def shutdown(self) -> None:
        return None

    def get_market_status(self, tracked_codes: set[str]) -> MarketStatusResponse:
        items: list[MarketCodeStatus] = []
        for code in sorted(tracked_codes):
            daily_dates = sorted(trade_date for (bar_code, trade_date) in self._daily_bars if bar_code == code)
            five_dates = sorted(trade_date for (bar_code, trade_date) in self._five_minute_bars if bar_code == code)
            latest_five = None
            five_rows = []
            if five_dates:
                latest_five = five_dates[-1]
                five_rows = self._five_minute_bars[(code, latest_five)]
            items.append(
                MarketCodeStatus(
                    code=code,
                    latest_daily_bar_date=daily_dates[-1] if daily_dates else None,
                    latest_five_minute_bar_date=latest_five,
                    five_minute_bar_count=len(five_rows),
                    last_five_minute_bar_time=five_rows[-1].bar_time if five_rows else None,
                )
            )
        return MarketStatusResponse(tracked_codes=sorted(tracked_codes), codes=items)

    def get_market_bars(self, code: str, trade_date: date | None = None) -> MarketBarsResponse:
        target_date = trade_date
        if target_date is None:
            daily_dates = sorted(current_date for (bar_code, current_date) in self._daily_bars if bar_code == code)
            five_dates = sorted(current_date for (bar_code, current_date) in self._five_minute_bars if bar_code == code)
            target_date = five_dates[-1] if five_dates else (daily_dates[-1] if daily_dates else None)
        if target_date is None:
            raise NotFoundError(f"No market bars available for {code}")
        return MarketBarsResponse(
            code=code,
            trade_date=target_date,
            daily_bar=self._daily_bars.get((code, target_date)),
            five_minute_bars=self._five_minute_bars.get((code, target_date), []),
        )

    def get_market_bars_or_none(self, code: str) -> MarketBarsResponse | None:
        try:
            return self.get_market_bars(code)
        except NotFoundError:
            return None

    def _stored_daily_dates(self, code: str, start_date: date, end_date: date) -> set[date]:
        dates: set[date] = set()
        current = start_date
        while current <= end_date:
            rows = self.storage.load_daily_bar(current) or []
            if any(bar.code == code for bar in rows):
                dates.add(current)
            current = current.fromordinal(current.toordinal() + 1)
        return dates

    def _stored_five_minute_dates(self, code: str, start_date: date, end_date: date) -> set[date]:
        dates: set[date] = set()
        current = start_date
        while current <= end_date:
            rows = self.storage.load_five_minute_bars(current) or []
            if any(bar.code == code for bar in rows):
                dates.add(current)
            current = current.fromordinal(current.toordinal() + 1)
        return dates
