"""Market provider primitives and market-data service."""

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Protocol
from uuid import uuid4

import baostock as bs

from quant_arena.schemas import CodeRefreshResponse, CodeSearchResponse, MarketBarsResponse, MarketCodeStatus, MarketParseJobResponse, MarketParseResponse, MarketRangeParseRequest, MarketStatusResponse
from quant_arena.clock import SHANGHAI_TZ, now_shanghai
from quant_arena.config import AppConfig
from quant_arena.errors import BadRequestError, NotFoundError
from quant_arena.models import (
    CodeNameEntry,
    DailyBar,
    DataParserJobEntry,
    FiveMinuteBar,
    DataParserJobConfig,
    QuoteSnapshot,
)
from quant_arena.storage import StorageService


class MarketDataProvider(Protocol):
    """
    Protocol for market data providers.

    设计理念：
    - 前端用户需要浏览某支股票的 k 线图
    - 前端用户需要能爬取历史数据并查看进度，最好能断点恢复
    - 另一种爬取数据模式是每天实时更新 5min 数据，等股市关门后更新当天 daily 数据
    - 实时更新 5min 数据的逻辑应该由服务器启动完成，这里只暴露接口显式爬取
    - 实时更新和历史爬取都应该使用相同的接口创建任务，因为非常需要查看进度的功能，并且实现几乎一样
    - 目前数据爬取不能指定股票代码，如果 5min 爬取任务无法按时完成，会加入更小的名单的功能
    - Arena 需要能查询最新 5min 价格来判断是否成交
    - 不应该把数据都存在内存里，否则会炸

    重要细节：
    - baostock 接口只支持返回某一支股票的一段时间，不支持返回某一些股票的一天
    - 所以爬取数据应该枚举股票代码而不是日期
    - 有些日期不开市，不会也不应该创建对应日期的数据目录
    """

    def get_code_names(self) -> list[CodeNameEntry]:
        """Return the provider's current code-name snapshot without refreshing."""

    def refresh_code_names(self) -> None:
        """Refresh code names using current date with backward logic."""

    def get_daily_bars(self, codes: list[str], trade_date: date) -> dict[str, DailyBar | None]:
        """Return one daily bar per code for the requested date."""

    def get_five_minute_bars(self, codes: list[str], trade_date: date) -> dict[str, list[FiveMinuteBar] | None]:
        """Return 5-minute bars per code for the requested date."""

    def create_data_parser_job(self, config: DataParserJobConfig) -> DataParserJobEntry:
        """Create a data parser job and return its entry."""

    def list_data_parser_jobs(self) -> list[DataParserJobEntry]:
        """Return the list of all created data parser jobs."""


class BaoStockMarketDataProvider:
    """Thin baostock-backed provider."""

    def __init__(self):
        self._code_names_cache: list[CodeNameEntry] = []

    def get_code_names(self) -> list[CodeNameEntry]:
        return list(self._code_names_cache)

    def refresh_code_names(self) -> list[CodeNameEntry]:
        today = now_shanghai().astimezone(SHANGHAI_TZ).date()
        login_result = bs.login()
        if login_result.error_code != "0":
            raise RuntimeError(f"baostock login failed: {login_result.error_msg}")
        try:
            for offset in range(0, 8):
                target_date = today - timedelta(days=offset)
                result = bs.query_all_stock(target_date.isoformat())
                if result.error_code != "0":
                    raise RuntimeError(f"baostock all-stock query failed: {result.error_msg}")

                entries: list[CodeNameEntry] = []
                while result.next():
                    row = result.get_row_data()
                    entries.append(
                        CodeNameEntry(
                            code=row[0],
                            name=row[2] or row[0],
                        )
                    )
                if entries:
                    self._code_names_cache = entries
                    return list(entries)
            return []
        finally:
            bs.logout()

    def get_daily_bars(self, codes: list[str], trade_date: date) -> dict[str, DailyBar]:
        if not codes:
            return {}

        login_result = bs.login()
        if login_result.error_code != "0":
            raise RuntimeError(f"baostock login failed: {login_result.error_msg}")
        try:
            bars: dict[str, DailyBar] = {}
            for code in codes:
                result = bs.query_history_k_data_plus(
                    code,
                    "code,date,open,high,low,close,preclose,volume,amount",
                    start_date=trade_date.isoformat(),
                    end_date=trade_date.isoformat(),
                    frequency="d",
                    adjustflag="3",
                )
                if result.error_code != "0":
                    raise RuntimeError(f"baostock daily bar query failed for {code}: {result.error_msg}")
                while result.next():
                    row = result.get_row_data()
                    bars[code] = DailyBar(
                        code=row[0],
                        trade_date=date.fromisoformat(row[1]),
                        open_price=float(row[2] or 0),
                        high_price=float(row[3] or 0),
                        low_price=float(row[4] or 0),
                        close_price=float(row[5] or 0),
                        prev_close=float(row[6] or 0),
                        volume=float(row[7] or 0),
                        amount=float(row[8] or 0),
                    )
            return bars
        finally:
            bs.logout()

    def get_five_minute_bars(self, codes: list[str], trade_date: date) -> dict[str, list[FiveMinuteBar]]:
        if not codes:
            return {}

        login_result = bs.login()
        if login_result.error_code != "0":
            raise RuntimeError(f"baostock login failed: {login_result.error_msg}")
        try:
            payload: dict[str, list[FiveMinuteBar]] = {}
            for code in codes:
                result = bs.query_history_k_data_plus(
                    code,
                    "code,date,time,open,high,low,close,volume,amount",
                    start_date=trade_date.isoformat(),
                    end_date=trade_date.isoformat(),
                    frequency="5",
                    adjustflag="3",
                )
                if result.error_code != "0":
                    raise RuntimeError(f"baostock 5-minute bar query failed for {code}: {result.error_msg}")
                bars: list[FiveMinuteBar] = []
                while result.next():
                    row = result.get_row_data()
                    timestamp = row[2][:14]
                    bar_time = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI_TZ)
                    bars.append(
                        FiveMinuteBar(
                            code=row[0],
                            trade_date=date.fromisoformat(row[1]),
                            bar_time=bar_time,
                            open_price=float(row[3] or 0),
                            high_price=float(row[4] or 0),
                            low_price=float(row[5] or 0),
                            close_price=float(row[6] or 0),
                            volume=float(row[7] or 0),
                            amount=float(row[8] or 0),
                        )
                    )
                payload[code] = bars
            return payload
        finally:
            bs.logout()

    def parse_historical_data(self, request: HistoricalMarketDataRequest) -> HistoricalMarketData:
        daily_codes = sorted(set(request.daily_codes))
        five_minute_codes = sorted(set(request.five_minute_codes))
        if not daily_codes and not five_minute_codes:
            return HistoricalMarketData(daily_bars=[], five_minute_bars=[])

        login_result = bs.login()
        if login_result.error_code != "0":
            raise RuntimeError(f"baostock login failed: {login_result.error_msg}")
        try:
            daily_bars: list[DailyBar] = []
            for code in daily_codes:
                result = bs.query_history_k_data_plus(
                    code,
                    "code,date,open,high,low,close,preclose,volume,amount",
                    start_date=request.start_date.isoformat(),
                    end_date=request.end_date.isoformat(),
                    frequency="d",
                    adjustflag="3",
                )
                if result.error_code != "0":
                    raise RuntimeError(f"baostock daily range query failed for {code}: {result.error_msg}")
                while result.next():
                    row = result.get_row_data()
                    daily_bars.append(
                        DailyBar(
                            code=row[0],
                            trade_date=date.fromisoformat(row[1]),
                            open_price=float(row[2] or 0),
                            high_price=float(row[3] or 0),
                            low_price=float(row[4] or 0),
                            close_price=float(row[5] or 0),
                            prev_close=float(row[6] or 0),
                            volume=float(row[7] or 0),
                            amount=float(row[8] or 0),
                        )
                    )

            five_minute_bars: list[FiveMinuteBar] = []
            for code in five_minute_codes:
                result = bs.query_history_k_data_plus(
                    code,
                    "code,date,time,open,high,low,close,volume,amount",
                    start_date=request.start_date.isoformat(),
                    end_date=request.end_date.isoformat(),
                    frequency="5",
                    adjustflag="3",
                )
                if result.error_code != "0":
                    raise RuntimeError(f"baostock 5-minute range query failed for {code}: {result.error_msg}")
                while result.next():
                    row = result.get_row_data()
                    timestamp = row[2][:14]
                    five_minute_bars.append(
                        FiveMinuteBar(
                            code=row[0],
                            trade_date=date.fromisoformat(row[1]),
                            bar_time=datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI_TZ),
                            open_price=float(row[3] or 0),
                            high_price=float(row[4] or 0),
                            low_price=float(row[5] or 0),
                            close_price=float(row[6] or 0),
                            volume=float(row[7] or 0),
                            amount=float(row[8] or 0),
                        )
                    )

            return HistoricalMarketData(
                daily_bars=daily_bars,
                five_minute_bars=five_minute_bars,
            )
        finally:
            bs.logout()


class MarketService:
    """Owns market-data refresh, persistence, and read APIs."""

    def __init__(self, config: AppConfig, storage_service: StorageService, provider: MarketDataProvider):
        self.config = config
        self.storage_service = storage_service
        self.provider = provider
        self._jobs: dict[str, MarketParseJobResponse] = {}
        self._jobs_lock = asyncio.Lock()
        self._job_tasks: dict[str, asyncio.Task[None]] = {}
        self._code_names_cache: dict[str, CodeNameEntry] = {}
        self._code_names_cache_refreshed_at: datetime | None = None

    def refresh_quotes(self, codes: list[str]) -> dict[str, QuoteSnapshot]:
        normalized_codes = sorted(set(codes))
        if not normalized_codes:
            return {}
        quotes: dict[str, QuoteSnapshot] = {}
        for code in normalized_codes:
            quote = self._load_latest_quote(code)
            if quote is not None:
                quotes[code] = quote
        return quotes

    def get_latest_quote(self, code: str) -> QuoteSnapshot:
        quote = self._load_latest_quote(code)
        if quote is None:
            raise NotFoundError(f"No on-disk market bars available for {code}")
        return quote

    def tracked_codes(self) -> set[str]:
        return set(self._load_code_names_map().keys())

    def refresh_code_names_if_needed(self, now: datetime | None = None) -> CodeRefreshResponse | None:
        if not self.config.enable_code_name_refresh:
            return None
        return self.refresh_code_names(force=False, now=now)

    def refresh_code_names(self, force: bool = False, now: datetime | None = None) -> CodeRefreshResponse:
        timestamp = now or now_shanghai()
        local_today = timestamp.astimezone(SHANGHAI_TZ).date()
        last_refreshed_at = self.storage_service.code_names_last_refreshed_at()
        if not force and last_refreshed_at is not None and last_refreshed_at.astimezone(SHANGHAI_TZ).date() >= local_today:
            return CodeRefreshResponse(
                refreshed_at=last_refreshed_at,
                entry_count=len(self._load_code_names_map()),
            )

        entries = self.provider.refresh_code_names()
        if entries:
            self.storage_service.save_code_names(entries)
            self._replace_code_names_cache(entries)
        refetched_at = self.storage_service.code_names_last_refreshed_at() or timestamp
        return CodeRefreshResponse(
            refreshed_at=refetched_at,
            entry_count=len(entries),
        )

    def search_code_names(self, query: str = "", page: int = 1, page_size: int = 20) -> CodeSearchResponse:
        normalized_page = max(page, 1)
        normalized_page_size = min(max(page_size, 1), 100)
        items = list(self._load_code_names_map().values())
        needle = query.strip().lower()
        if needle:
            items = [
                item
                for item in items
                if needle in item.code.lower() or needle in item.name.lower()
            ]
        total = len(items)
        start = (normalized_page - 1) * normalized_page_size
        end = start + normalized_page_size
        return CodeSearchResponse(
            query=query,
            page=normalized_page,
            page_size=normalized_page_size,
            total=total,
            items=items[start:end],
            last_refreshed_at=self.storage_service.code_names_last_refreshed_at(),
            auto_refresh_enabled=self.config.enable_code_name_refresh,
        )

    def sync_market_data(self, tracked_codes: set[str], now: datetime | None = None) -> None:
        timestamp = now or now_shanghai()
        self.refresh_code_names_if_needed(now=timestamp)
        if not tracked_codes:
            return

        ordered_codes = sorted(tracked_codes)
        local_now = timestamp.astimezone(SHANGHAI_TZ)
        quotes = self.refresh_quotes(ordered_codes)
        trade_dates = {quote.trade_date for quote in quotes.values()}
        if local_now.date() in trade_dates and self._is_market_open(local_now):
            five_minute_bars_by_code = self.provider.get_five_minute_bars(ordered_codes, local_now.date())
            self.storage_service.save_five_minute_bar_rows(
                [bar for bars in five_minute_bars_by_code.values() for bar in bars]
            )
        if local_now.date() in trade_dates and self._is_after_market_close(local_now):
            self.storage_service.save_daily_bar_rows(list(self.provider.get_daily_bars(ordered_codes, local_now.date()).values()))

    def parse_today_market_data_if_missing(self, tracked_codes: set[str], now: datetime | None = None) -> MarketParseResponse:
        timestamp = now or now_shanghai()
        local_today = timestamp.astimezone(SHANGHAI_TZ).date()
        if not tracked_codes:
            return MarketParseResponse(
                trade_date=local_today,
                tracked_codes=[],
                parsed_daily_codes=[],
                parsed_five_minute_codes=[],
            )

        today_codes = sorted(tracked_codes)
        daily_bars = self.storage_service.load_daily_bar(local_today) or []
        five_minute_bars = self.storage_service.load_five_minute_bars(local_today) or []
        daily_codes = {bar.code for bar in daily_bars}
        five_minute_codes = {bar.code for bar in five_minute_bars}
        missing_daily_codes = [code for code in today_codes if code not in daily_codes]
        missing_five_minute_codes = [code for code in today_codes if code not in five_minute_codes]

        if missing_daily_codes:
            self.storage_service.save_daily_bar_rows(list(self.provider.get_daily_bars(missing_daily_codes, local_today).values()))
        if missing_five_minute_codes:
            five_minute_bars_by_code = self.provider.get_five_minute_bars(missing_five_minute_codes, local_today)
            self.storage_service.save_five_minute_bar_rows(
                [bar for bars in five_minute_bars_by_code.values() for bar in bars]
            )

        return MarketParseResponse(
            trade_date=local_today,
            tracked_codes=today_codes,
            parsed_daily_codes=missing_daily_codes,
            parsed_five_minute_codes=missing_five_minute_codes,
        )

    async def start_range_parse_job(
        self,
        tracked_codes: set[str],
        request: MarketRangeParseRequest
    ) -> MarketParseJobResponse:
        if request.end_date < request.start_date:
            raise BadRequestError("end_date must be on or after start_date")

        normalized_codes = sorted(tracked_codes)
        created_at = now_shanghai()
        job = MarketParseJobResponse(
            job_id=uuid4().hex,
            status="pending",
            start_date=request.start_date,
            end_date=request.end_date,
            tracked_codes_total=len(normalized_codes),
            tracked_codes_completed=0,
            created_at=created_at,
            message="queued",
        )
        async with self._jobs_lock:
            self._jobs[job.job_id] = job
        task = asyncio.create_task(
            self._run_range_parse_job(job.job_id, normalized_codes),
            name=f"market-parse-{job.job_id[:8]}",
        )
        self._job_tasks[job.job_id] = task
        task.add_done_callback(lambda _: self._job_tasks.pop(job.job_id, None))
        return await self.get_parse_job(job.job_id)

    async def list_parse_jobs(self) -> list[MarketParseJobResponse]:
        async with self._jobs_lock:
            jobs = [job.model_copy() for job in self._jobs.values()]
        return sorted(jobs, key=lambda item: item.created_at, reverse=True)

    async def get_parse_job(self, job_id: str) -> MarketParseJobResponse:
        async with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise NotFoundError(f"Unknown parse job: {job_id}")
            return job.model_copy()

    async def shutdown(self) -> None:
        tasks = list(self._job_tasks.values())
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._job_tasks.clear()

    def get_market_status(self, tracked_codes: set[str]) -> MarketStatusResponse:
        ordered_tracked_codes = sorted(tracked_codes)
        codes = ordered_tracked_codes
        items: list[MarketCodeStatus] = []
        for code in codes:
            latest_daily_date = self._latest_daily_bar_date(code)
            latest_five_minute_date = self._latest_five_minute_bar_date(code)
            five_minute_bars: list[FiveMinuteBar] = []
            if latest_five_minute_date is not None:
                day_bars = self.storage_service.load_five_minute_bars(latest_five_minute_date) or []
                five_minute_bars = [bar for bar in day_bars if bar.code == code]
            items.append(
                MarketCodeStatus(
                    code=code,
                    latest_daily_bar_date=latest_daily_date,
                    latest_five_minute_bar_date=latest_five_minute_date,
                    five_minute_bar_count=len(five_minute_bars),
                    last_five_minute_bar_time=five_minute_bars[-1].bar_time if five_minute_bars else None,
                )
            )
        return MarketStatusResponse(tracked_codes=ordered_tracked_codes, codes=items)

    def get_market_bars(self, code: str, trade_date: date | None = None) -> MarketBarsResponse:
        target_date = trade_date or self._latest_five_minute_bar_date(code) or self._latest_daily_bar_date(code)
        if target_date is None:
            raise NotFoundError(f"No market bars available for {code}")
        daily_bars = self.storage_service.load_daily_bar(target_date) or []
        five_minute_bars = self.storage_service.load_five_minute_bars(target_date) or []
        return MarketBarsResponse(
            code=code,
            trade_date=target_date,
            daily_bar=next((bar for bar in daily_bars if bar.code == code), None),
            five_minute_bars=[bar for bar in five_minute_bars if bar.code == code],
        )

    def _load_latest_quote(self, code: str) -> QuoteSnapshot | None:
        latest_five_minute_date = self._latest_five_minute_bar_date(code)
        latest_daily_date = self._latest_daily_bar_date(code)
        if latest_five_minute_date is None and latest_daily_date is None:
            return None

        code_name = self._load_code_names_map().get(code)
        if latest_five_minute_date is not None and (
            latest_daily_date is None or latest_five_minute_date >= latest_daily_date
        ):
            day_bars = self.storage_service.load_five_minute_bars(latest_five_minute_date) or []
            five_minute_bars = [bar for bar in day_bars if bar.code == code]
            if not five_minute_bars:
                return None
            latest_bar = five_minute_bars[-1]
            daily_bars = self.storage_service.load_daily_bar(latest_five_minute_date) or []
            reference_daily_bar = next((bar for bar in daily_bars if bar.code == code), None)
            if reference_daily_bar is None:
                return None
            return QuoteSnapshot(
                code=code,
                name=code_name.name if code_name is not None else code,
                trade_date=latest_bar.trade_date,
                as_of=latest_bar.bar_time,
                last_price=latest_bar.close_price,
                limit_up=round(reference_daily_bar.prev_close * 1.1, 2),
                limit_down=round(reference_daily_bar.prev_close * 0.9, 2),
            )

        if latest_daily_date is None:
            return None
        day_bars = self.storage_service.load_daily_bar(latest_daily_date) or []
        latest_daily_bar = next((bar for bar in day_bars if bar.code == code), None)
        if latest_daily_bar is None:
            return None
        return QuoteSnapshot(
            code=code,
            name=code_name.name if code_name is not None else code,
            trade_date=latest_daily_bar.trade_date,
            as_of=datetime.combine(latest_daily_bar.trade_date, time(15, 0), tzinfo=SHANGHAI_TZ),
            last_price=latest_daily_bar.close_price,
            limit_up=round(latest_daily_bar.prev_close * 1.1, 2),
            limit_down=round(latest_daily_bar.prev_close * 0.9, 2),
        )

    def _load_code_names_map(self) -> dict[str, CodeNameEntry]:
        last_refreshed_at = self.storage_service.code_names_last_refreshed_at()
        if last_refreshed_at is None:
            self._code_names_cache = {}
            self._code_names_cache_refreshed_at = None
            return self._code_names_cache
        if self._code_names_cache_refreshed_at == last_refreshed_at:
            return self._code_names_cache
        entries = self.storage_service.load_code_names() or []
        self._code_names_cache = {entry.code: entry for entry in entries}
        self._code_names_cache_refreshed_at = last_refreshed_at
        return self._code_names_cache

    def _replace_code_names_cache(self, entries: list[CodeNameEntry]) -> None:
        self._code_names_cache = {entry.code: entry for entry in entries}
        self._code_names_cache_refreshed_at = self.storage_service.code_names_last_refreshed_at()

    def _latest_daily_bar_date(self, code: str) -> date | None:
        candidates: list[date] = []
        for trade_date in self._stored_trade_dates():
            daily_bars = self.storage_service.load_daily_bar(trade_date)
            if daily_bars is not None and any(bar.code == code for bar in daily_bars):
                candidates.append(trade_date)
        return candidates[-1] if candidates else None

    def _latest_five_minute_bar_date(self, code: str) -> date | None:
        candidates: list[date] = []
        for trade_date in self._stored_trade_dates():
            five_minute_bars = self.storage_service.load_five_minute_bars(trade_date)
            if five_minute_bars is not None and any(bar.code == code for bar in five_minute_bars):
                candidates.append(trade_date)
        return candidates[-1] if candidates else None

    def _stored_trade_dates(self) -> list[date]:
        if not self.storage_service.market_bars_dir.exists():
            return []
        dates: list[date] = []
        for path in sorted(self.storage_service.market_bars_dir.iterdir()):
            if not path.is_dir():
                continue
            dates.append(date.fromisoformat(path.name))
        return dates

    async def _run_range_parse_job(self, job_id: str, tracked_codes: list[str]) -> None:
        started_at = now_shanghai()
        await self._update_job(
            job_id,
            status="running",
            started_at=started_at,
            message="running",
        )
        try:
            for index, code in enumerate(tracked_codes, start=1):
                job = await self.get_parse_job(job_id)
                await self._update_job(
                    job_id,
                    current_code=code,
                    current_step="checking",
                    tracked_codes_completed=index - 1,
                    message=f"processing {code}",
                )

                missing_daily_dates = self._missing_daily_dates(code, job)
                missing_five_minute_dates = self._missing_five_minute_dates(code, job)

                if not missing_daily_dates:
                    await self._increment_job(job_id, skipped_daily_codes=1)
                if not missing_five_minute_dates:
                    await self._increment_job(job_id, skipped_five_minute_codes=1)

                if missing_daily_dates or missing_five_minute_dates:
                    current_step = "fetching history"
                    if missing_daily_dates and not missing_five_minute_dates:
                        current_step = "fetching daily"
                    if missing_five_minute_dates and not missing_daily_dates:
                        current_step = "fetching 5min"
                    await self._update_job(job_id, current_step=current_step)
                    parsed = self.provider.parse_historical_data(
                        HistoricalMarketDataRequest(
                            start_date=job.start_date,
                            end_date=job.end_date,
                            daily_codes=[code] if missing_daily_dates else [],
                            five_minute_codes=[code] if missing_five_minute_dates else [],
                        )
                    )
                    self.storage_service.save_daily_bar_rows(parsed.daily_bars)
                    self.storage_service.save_five_minute_bar_rows(parsed.five_minute_bars)
                    await self._increment_job(
                        job_id,
                        daily_rows_written=len(parsed.daily_bars),
                        five_minute_rows_written=len(parsed.five_minute_bars),
                    )

                await self._update_job(
                    job_id,
                    tracked_codes_completed=index,
                    current_step="completed code",
                )
                await asyncio.sleep(0)

            await self._update_job(
                job_id,
                status="completed",
                finished_at=now_shanghai(),
                current_code=None,
                current_step=None,
                message="completed",
            )
        except asyncio.CancelledError:
            await self._update_job(
                job_id,
                status="cancelled",
                finished_at=now_shanghai(),
                current_code=None,
                current_step=None,
                message="cancelled",
            )
            raise
        except Exception as exc:
            await self._update_job(
                job_id,
                status="failed",
                finished_at=now_shanghai(),
                current_step=None,
                message="failed",
                error=str(exc),
            )

    def _missing_daily_dates(self, code: str, job: MarketParseJobResponse) -> list[date]:
        return [
            current_date
            for current_date in self._iter_dates(job.start_date, job.end_date)
            if code not in {bar.code for bar in (self.storage_service.load_daily_bar(current_date) or [])}
        ]

    def _missing_five_minute_dates(self, code: str, job: MarketParseJobResponse) -> list[date]:
        return [
            current_date
            for current_date in self._iter_dates(job.start_date, job.end_date)
            if code not in {bar.code for bar in (self.storage_service.load_five_minute_bars(current_date) or [])}
        ]

    async def _update_job(self, job_id: str, **updates: object) -> None:
        async with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._jobs[job_id] = job.model_copy(update=updates)

    async def _increment_job(
        self,
        job_id: str,
        daily_rows_written: int = 0,
        five_minute_rows_written: int = 0,
        skipped_daily_codes: int = 0,
        skipped_five_minute_codes: int = 0,
    ) -> None:
        async with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._jobs[job_id] = job.model_copy(
                update={
                    "daily_rows_written": job.daily_rows_written + daily_rows_written,
                    "five_minute_rows_written": job.five_minute_rows_written + five_minute_rows_written,
                    "skipped_daily_codes": job.skipped_daily_codes + skipped_daily_codes,
                    "skipped_five_minute_codes": job.skipped_five_minute_codes + skipped_five_minute_codes,
                }
            )

    @staticmethod
    def _iter_dates(start_date: date, end_date: date) -> list[date]:
        total_days = (end_date - start_date).days
        return [start_date + timedelta(days=offset) for offset in range(total_days + 1)]

    @staticmethod
    def _is_market_open(moment: datetime) -> bool:
        current = moment.timetz().replace(tzinfo=None)
        return (time(9, 30) <= current < time(11, 30)) or (time(13, 0) <= current < time(15, 0))

    @staticmethod
    def _is_after_market_close(moment: datetime) -> bool:
        current = moment.timetz().replace(tzinfo=None)
        return current >= time(15, 0)
