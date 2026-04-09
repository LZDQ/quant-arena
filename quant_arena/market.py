"""Market provider primitives and market-data service."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

import baostock as bs
from fastapi import HTTPException

from quant_arena.config import AppConfig
from quant_arena.models import (
	CodeNameEntry,
	CodeRefreshResponse,
	CodeSearchResponse,
	DailyBar,
	FiveMinuteBar,
	MarketBarsResponse,
	MarketCodeStatus,
	MarketParseResponse,
	MarketStatusResponse,
	QuoteSnapshot,
)
from quant_arena.storage import ArenaStorage


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class MarketDataProvider(Protocol):
	"""Protocol for market data providers."""

	def get_code_names(self, day: date) -> list[CodeNameEntry]:
		"""Return code-name rows for the given day."""

	def get_latest_quotes(self, codes: list[str]) -> dict[str, QuoteSnapshot]:
		"""Return latest quotes for the requested codes."""

	def get_daily_bars(self, codes: list[str], trade_date: date) -> dict[str, DailyBar]:
		"""Return one daily bar per code for the requested date."""

	def get_five_minute_bars(self, codes: list[str], trade_date: date) -> dict[str, list[FiveMinuteBar]]:
		"""Return 5-minute bars per code for the requested date."""


class BaoStockMarketDataProvider:
	"""Thin baostock-backed provider."""

	def get_code_names(self, day: date) -> list[CodeNameEntry]:
		login_result = bs.login()
		if login_result.error_code != "0":
			raise RuntimeError(f"baostock login failed: {login_result.error_msg}")
		try:
			result = bs.query_all_stock(day.isoformat())
			if result.error_code != "0":
				raise RuntimeError(f"baostock all-stock query failed: {result.error_msg}")

			entries: list[CodeNameEntry] = []
			while result.next():
				row = result.get_row_data()
				entries.append(
					CodeNameEntry(
						code=row[0],
						trade_status=row[1] or None,
						name=row[2] or None,
					)
				)
			return entries
		finally:
			bs.logout()

	def get_latest_quotes(self, codes: list[str]) -> dict[str, QuoteSnapshot]:
		if not codes:
			return {}

		login_result = bs.login()
		if login_result.error_code != "0":
			raise RuntimeError(f"baostock login failed: {login_result.error_msg}")
		try:
			result = bs.query_stock_latest_info(",".join(codes))
			if result.error_code != "0":
				raise RuntimeError(f"baostock latest info query failed: {result.error_msg}")

			quotes: dict[str, QuoteSnapshot] = {}
			now = datetime.now(timezone.utc)
			while result.next():
				row = result.get_row_data()
				code = row[0]
				close = float(row[5])
				prev_close = float(row[6]) if row[6] else close
				limit_up = round(prev_close * 1.1, 2)
				limit_down = round(prev_close * 0.9, 2)
				quotes[code] = QuoteSnapshot(
					code=code,
					name=row[1] or None,
					trade_date=date.fromisoformat(row[2]),
					as_of=now,
					last_price=close,
					prev_close=prev_close,
					limit_up=limit_up,
					limit_down=limit_down,
				)
			return quotes
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


class MarketService:
	"""Owns market-data refresh, persistence, and read APIs."""

	def __init__(self, config: AppConfig, storage: ArenaStorage, provider: MarketDataProvider):
		self.config = config
		self.storage = storage
		self.provider = provider

	def refresh_quotes(self, codes: list[str]) -> dict[str, QuoteSnapshot]:
		normalized_codes = sorted(set(codes))
		if not normalized_codes:
			return {}
		return self.provider.get_latest_quotes(normalized_codes)

	def refresh_code_names_if_needed(self, now: datetime | None = None) -> CodeRefreshResponse | None:
		if not self.config.enable_code_name_refresh:
			return None
		return self.refresh_code_names(force=False, now=now)

	def refresh_code_names(self, force: bool = False, now: datetime | None = None) -> CodeRefreshResponse:
		timestamp = now or datetime.now(timezone.utc)
		local_today = timestamp.astimezone(SHANGHAI_TZ).date()
		last_refreshed_at = self.storage.code_names_last_refreshed_at()
		if not force and last_refreshed_at is not None and last_refreshed_at.astimezone(SHANGHAI_TZ).date() >= local_today:
			return CodeRefreshResponse(
				refreshed_at=last_refreshed_at,
				entry_count=self.storage.search_code_names("", page=1, page_size=1)[0],
			)

		entries = self._fetch_latest_non_empty_code_names(local_today)
		if entries:
			self.storage.save_code_names(entries)
		refetched_at = self.storage.code_names_last_refreshed_at() or timestamp
		return CodeRefreshResponse(
			refreshed_at=refetched_at,
			entry_count=len(entries),
		)

	def search_code_names(self, query: str = "", page: int = 1, page_size: int = 20) -> CodeSearchResponse:
		normalized_page = max(page, 1)
		normalized_page_size = min(max(page_size, 1), 100)
		total, items = self.storage.search_code_names(query, normalized_page, normalized_page_size)
		return CodeSearchResponse(
			query=query,
			page=normalized_page,
			page_size=normalized_page_size,
			total=total,
			items=items,
			last_refreshed_at=self.storage.code_names_last_refreshed_at(),
			auto_refresh_enabled=self.config.enable_code_name_refresh,
		)

	def sync_market_data(self, tracked_codes: list[str], now: datetime | None = None) -> None:
		timestamp = now or datetime.now(timezone.utc)
		self.refresh_code_names_if_needed(now=timestamp)
		if not tracked_codes:
			return

		local_now = timestamp.astimezone(SHANGHAI_TZ)
		quotes = self.refresh_quotes(tracked_codes)
		trade_dates = {quote.trade_date for quote in quotes.values()}
		if local_now.date() in trade_dates and self._is_market_open(local_now):
			self.storage.save_five_minute_bars(self.provider.get_five_minute_bars(tracked_codes, local_now.date()))
		if local_now.date() in trade_dates and self._is_after_market_close(local_now):
			self.storage.save_daily_bars(self.provider.get_daily_bars(tracked_codes, local_now.date()))

	def parse_today_market_data_if_missing(self, tracked_codes: list[str], now: datetime | None = None) -> MarketParseResponse:
		timestamp = now or datetime.now(timezone.utc)
		local_today = timestamp.astimezone(SHANGHAI_TZ).date()
		if not tracked_codes:
			return MarketParseResponse(
				trade_date=local_today,
				tracked_codes=[],
				parsed_daily_codes=[],
				parsed_five_minute_codes=[],
			)

		quotes = self.refresh_quotes(tracked_codes)
		today_codes = sorted(code for code, quote in quotes.items() if quote.trade_date == local_today)
		missing_daily_codes = [code for code in today_codes if self.storage.load_daily_bar(code, local_today) is None]
		missing_five_minute_codes = [code for code in today_codes if not self.storage.load_five_minute_bars(code, local_today)]

		if missing_daily_codes:
			self.storage.save_daily_bars(self.provider.get_daily_bars(missing_daily_codes, local_today))
		if missing_five_minute_codes:
			self.storage.save_five_minute_bars(self.provider.get_five_minute_bars(missing_five_minute_codes, local_today))

		return MarketParseResponse(
			trade_date=local_today,
			tracked_codes=today_codes,
			parsed_daily_codes=missing_daily_codes,
			parsed_five_minute_codes=missing_five_minute_codes,
		)

	def get_market_status(self, tracked_codes: list[str]) -> MarketStatusResponse:
		codes = sorted(set(tracked_codes) | set(self.storage.list_market_codes()))
		items: list[MarketCodeStatus] = []
		for code in codes:
			latest_daily_date = self.storage.latest_daily_bar_date(code)
			latest_five_minute_date = self.storage.latest_five_minute_bar_date(code)
			five_minute_bars: list[FiveMinuteBar] = []
			if latest_five_minute_date is not None:
				five_minute_bars = self.storage.load_five_minute_bars(code, latest_five_minute_date)
			items.append(
				MarketCodeStatus(
					code=code,
					latest_daily_bar_date=latest_daily_date,
					latest_five_minute_bar_date=latest_five_minute_date,
					five_minute_bar_count=len(five_minute_bars),
					last_five_minute_bar_time=five_minute_bars[-1].bar_time if five_minute_bars else None,
				)
			)
		return MarketStatusResponse(tracked_codes=tracked_codes, codes=items)

	def get_market_bars(self, code: str, trade_date: date | None = None) -> MarketBarsResponse:
		target_date = trade_date or self.storage.latest_five_minute_bar_date(code) or self.storage.latest_daily_bar_date(code)
		if target_date is None:
			raise HTTPException(status_code=404, detail=f"No market bars available for {code}")
		return MarketBarsResponse(
			code=code,
			trade_date=target_date,
			daily_bar=self.storage.load_daily_bar(code, target_date),
			five_minute_bars=self.storage.load_five_minute_bars(code, target_date),
		)

	def _fetch_latest_non_empty_code_names(self, start_date: date) -> list[CodeNameEntry]:
		for offset in range(0, 8):
			target_date = start_date - timedelta(days=offset)
			entries = self.provider.get_code_names(target_date)
			if entries:
				return entries
		return self.storage.search_code_names("", page=1, page_size=1000000)[1]

	@staticmethod
	def _is_market_open(moment: datetime) -> bool:
		current = moment.timetz().replace(tzinfo=None)
		return (time(9, 30) <= current < time(11, 30)) or (time(13, 0) <= current < time(15, 0))

	@staticmethod
	def _is_after_market_close(moment: datetime) -> bool:
		current = moment.timetz().replace(tzinfo=None)
		return current >= time(15, 0)
