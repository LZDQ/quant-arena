"""Baostock-backed market data service."""

import asyncio
from datetime import date, datetime, timedelta

import baostock as bs

from quant_arena.clock import SHANGHAI_TZ, now_shanghai
from quant_arena.config import AppConfig
from quant_arena.errors import BadRequestError
from quant_arena.models import (
    CodeNameEntry,
    DailyBar,
    DataParserJobConfig,
    DataParserJobEntry,
    FiveMinuteBar,
    MarketBarsData,
    MarketParseResult,
)
from quant_arena.storage import StorageService


class BaostockMarketService:
    """
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

    def __init__(self, config: AppConfig, storage: StorageService):
        self.config = config
        self.storage = storage
        self._code_names_map: dict[str, str] | None = None
        self._parser_jobs: list[tuple[DataParserJobEntry, asyncio.Task[None]]] = []

    def get_code_names_mapping(self) -> dict[str, str] | None:
        """Return the provider's current code-name snapshot without refreshing."""
        if self._code_names_map is None:
            stored_code_names = self.storage.load_code_names()
            if stored_code_names is None:
                self._code_names_map = {
                    entry.code: entry.name
                    for entry in stored_code_names
                }
        return self._code_names_map

    def refresh_code_names(self) -> None:
        """Refresh code names using current date with backward logic."""
        today = now_shanghai().date()
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
                            name=row[2],
                        )
                    )
                if entries:
                    self.storage.save_code_names(entries)
                    self._code_names_map = {entry.code: entry.name for entry in entries}
                    return
        finally:
            bs.logout()

    def get_daily_bars(
        self,
        codes: list[str],
        trade_date: date
    ) -> dict[str, DailyBar | None]:
        """Return one daily bar per code for the requested date."""
        stored_bars = self.storage.load_daily_bar(trade_date) or []
        bars_by_code = {bar.code: bar for bar in stored_bars}
        return {code: bars_by_code.get(code) for code in codes}

    def get_five_minute_bars(
        self,
        codes: list[str],
        trade_date: date
    ) -> dict[str, list[FiveMinuteBar] | None]:
        """Return 5-minute bars per code for the requested date."""
        stored_bars = self.storage.load_five_minute_bars(trade_date) or []
        bars_by_code: dict[str, list[FiveMinuteBar]] = {}
        for bar in stored_bars:
            bars_by_code.setdefault(bar.code, []).append(bar)
        return {code: bars_by_code.get(code) for code in codes}

    def create_data_parser_job(self, config: DataParserJobConfig) -> DataParserJobEntry:
        """Create a data parser job and return its entry."""
        if config.end_date < config.start_date:
            raise BadRequestError("end_date must be on or after start_date")
        job = DataParserJobEntry(
            config=config,
            skipped=0 if config.skip_existing else None,
            parsed=0,
            error=None,
            start_time=now_shanghai(),
            finish_time=None,
        )
        task = asyncio.create_task(self._run_data_parser_job(job))
        self._parser_jobs.append((job, task))
        return job

    def list_data_parser_jobs(self) -> list[DataParserJobEntry]:
        """Return the list of all created data parser jobs."""
        return [job.model_copy() for job, _ in self._parser_jobs]

    def get_latest_prices(self, codes: list[str]) -> dict[str, float | None]:
        latest_trade_date, latest_bars = self._latest_five_minute_snapshot()
        if latest_trade_date is None or latest_bars is None:
            return {code: None for code in sorted(set(codes))}
        latest_by_code = {bar.code: bar.close_price for bar in latest_bars}
        prices: dict[str, float | None] = {}
        for code in sorted(set(codes)):
            prices[code] = latest_by_code.get(code)
        return prices

    def sync_market_data(self, tracked_codes: set[str], now: datetime | None = None) -> None:
        timestamp = now or now_shanghai()
        self._refresh_code_names_if_needed(timestamp)
        if not tracked_codes:
            return

        local_now = timestamp.astimezone(SHANGHAI_TZ)
        if self._is_market_open(local_now):
            five_minute_bars = self._load_all_five_minute_bars(local_now.date(), sorted(tracked_codes))
            self.storage.save_five_minute_bar_rows(five_minute_bars)
        if self._is_after_market_close(local_now):
            daily_bars = self._load_all_daily_bars(local_now.date(), sorted(tracked_codes))
            self.storage.save_daily_bar_rows(daily_bars)

    def parse_today_market_data_if_missing(self, tracked_codes: set[str], now: datetime | None = None) -> MarketParseResult:
        timestamp = now or now_shanghai()
        local_today = timestamp.astimezone(SHANGHAI_TZ).date()
        if not tracked_codes:
            return MarketParseResult(
                trade_date=local_today,
                tracked_codes=[],
                parsed_daily_codes=[],
                parsed_five_minute_codes=[],
            )

        today_codes = sorted(tracked_codes)
        daily_bars = self.storage.load_daily_bar(local_today) or []
        five_minute_bars = self.storage.load_five_minute_bars(local_today) or []
        daily_codes = {bar.code for bar in daily_bars}
        five_minute_codes = {bar.code for bar in five_minute_bars}
        missing_daily_codes = [code for code in today_codes if code not in daily_codes]
        missing_five_minute_codes = [code for code in today_codes if code not in five_minute_codes]

        if missing_daily_codes:
            self.storage.save_daily_bar_rows(self._load_all_daily_bars(local_today, missing_daily_codes))
        if missing_five_minute_codes:
            self.storage.save_five_minute_bar_rows(self._load_all_five_minute_bars(local_today, missing_five_minute_codes))

        return MarketParseResult(
            trade_date=local_today,
            tracked_codes=today_codes,
            parsed_daily_codes=missing_daily_codes,
            parsed_five_minute_codes=missing_five_minute_codes,
        )

    async def shutdown(self) -> None:
        tasks = [task for _, task in self._parser_jobs]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_data_parser_job(self, job: DataParserJobEntry) -> None:
        try:
            codes = sorted(self._code_names_map.keys()) if self._code_names_map is not None else []
            if not codes:
                self.refresh_code_names()
                codes = sorted(self._code_names_map.keys()) if self._code_names_map is not None else []

            for code in codes:
                missing_daily_dates = self._missing_daily_dates(code, job.config)
                missing_five_minute_dates = self._missing_five_minute_dates(code, job.config)
                should_fetch_daily = job.config.mode in {"daily", "both"}
                should_fetch_five_minute = job.config.mode in {"five_minute", "both"}

                if job.config.skip_existing:
                    if should_fetch_daily and not missing_daily_dates:
                        should_fetch_daily = False
                    if should_fetch_five_minute and not missing_five_minute_dates:
                        should_fetch_five_minute = False

                if not should_fetch_daily and not should_fetch_five_minute:
                    if job.skipped is not None:
                        job.skipped += 1
                    await asyncio.sleep(0)
                    continue

                if should_fetch_daily:
                    daily_dates = missing_daily_dates if job.config.skip_existing else self._trading_dates(job.config.start_date, job.config.end_date)
                    daily_bars = self._load_daily_bars_for_dates(code, daily_dates)
                    self.storage.save_daily_bar_rows(daily_bars)

                if should_fetch_five_minute:
                    five_minute_dates = missing_five_minute_dates if job.config.skip_existing else self._trading_dates(job.config.start_date, job.config.end_date)
                    five_minute_bars = self._load_five_minute_bars_for_dates(code, five_minute_dates)
                    self.storage.save_five_minute_bar_rows(five_minute_bars)

                job.parsed += 1
                await asyncio.sleep(0)
        except Exception as exc:
            job.error = str(exc)
        finally:
            job.finish_time = now_shanghai()

    def _trading_dates(self, start_date: date, end_date: date) -> list[date]:
        login_result = bs.login()
        if login_result.error_code != "0":
            raise RuntimeError(f"baostock login failed: {login_result.error_msg}")
        try:
            result = bs.query_trade_dates(start_date=start_date.isoformat(), end_date=end_date.isoformat())
            if result.error_code != "0":
                raise RuntimeError(f"baostock trade-date query failed: {result.error_msg}")
            dates: list[date] = []
            while result.next():
                row = result.get_row_data()
                if row[1] == "1":
                    dates.append(date.fromisoformat(row[0]))
            return dates
        finally:
            bs.logout()

    def _missing_daily_dates(self, code: str, config: DataParserJobConfig) -> list[date]:
        """TODO: remove this and persist trading dates just like code names with caching"""
        return [
            current_date
            for current_date in self._trading_dates(config.start_date, config.end_date)
            if code not in {bar.code for bar in (self.storage.load_daily_bar(current_date) or [])}
        ]

    def _missing_five_minute_dates(self, code: str, config: DataParserJobConfig) -> list[date]:
        return [
            current_date
            for current_date in self._trading_dates(config.start_date, config.end_date)
            if code not in {bar.code for bar in (self.storage.load_five_minute_bars(current_date) or [])}
        ]

    def get_market_bars_or_none(self, code: str) -> MarketBarsData | None:
        trade_date, latest_bars = self._latest_five_minute_snapshot()
        if trade_date is None or latest_bars is None:
            return None
        code_bars = [bar for bar in latest_bars if bar.code == code]
        if not code_bars:
            return None
        daily_bars = self.storage.load_daily_bar(trade_date) or []
        return MarketBarsData(
            code=code,
            trade_date=trade_date,
            daily_bar=next((bar for bar in daily_bars if bar.code == code), None),
            five_minute_bars=code_bars,
        )

    def _refresh_code_names_if_needed(self, timestamp: datetime) -> None:
        if not self.config.enable_code_name_refresh:
            return
        local_today = timestamp.astimezone(SHANGHAI_TZ).date()
        last_refreshed_at = self.storage.code_names_last_refreshed_at()
        if last_refreshed_at is not None and last_refreshed_at.astimezone(SHANGHAI_TZ).date() >= local_today:
            return
        self.refresh_code_names()
