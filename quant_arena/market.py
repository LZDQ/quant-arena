"""Market data provider interfaces."""

from datetime import date, datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

import baostock as bs

from quant_arena.models import DailyBar, FiveMinuteBar, QuoteSnapshot


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class MarketDataProvider(Protocol):
	"""Protocol for market data providers."""

	def get_latest_quotes(self, codes: list[str]) -> dict[str, QuoteSnapshot]:
		"""Return latest quotes for the requested codes."""

	def get_daily_bars(self, codes: list[str], trade_date: date) -> dict[str, DailyBar]:
		"""Return one daily bar per code for the requested date."""

	def get_five_minute_bars(self, codes: list[str], trade_date: date) -> dict[str, list[FiveMinuteBar]]:
		"""Return 5-minute bars per code for the requested date."""


class BaoStockMarketDataProvider:
	"""Thin baostock-backed provider."""

	def get_latest_quotes(self, codes: list[str]) -> dict[str, QuoteSnapshot]:
		if not codes:
			return {}

		login_result = bs.login()
		if getattr(login_result, "error_code", "0") != "0":
			raise RuntimeError(f"baostock login failed: {login_result.error_msg}")
		try:
			result = bs.query_stock_latest_info(",".join(codes))
			if getattr(result, "error_code", "0") != "0":
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
		if getattr(login_result, "error_code", "0") != "0":
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
				if getattr(result, "error_code", "0") != "0":
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
		if getattr(login_result, "error_code", "0") != "0":
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
				if getattr(result, "error_code", "0") != "0":
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


class StaticMarketDataProvider:
	"""In-memory provider used by tests."""

	def __init__(
		self,
		quotes: dict[str, QuoteSnapshot],
		daily_bars: dict[tuple[str, date], DailyBar] | None = None,
		five_minute_bars: dict[tuple[str, date], list[FiveMinuteBar]] | None = None,
	):
		self._quotes = quotes
		self._daily_bars = daily_bars or {}
		self._five_minute_bars = five_minute_bars or {}

	def get_latest_quotes(self, codes: list[str]) -> dict[str, QuoteSnapshot]:
		return {code: self._quotes[code] for code in codes if code in self._quotes}

	def get_daily_bars(self, codes: list[str], trade_date: date) -> dict[str, DailyBar]:
		return {
			code: self._daily_bars[(code, trade_date)]
			for code in codes
			if (code, trade_date) in self._daily_bars
		}

	def get_five_minute_bars(self, codes: list[str], trade_date: date) -> dict[str, list[FiveMinuteBar]]:
		return {
			code: self._five_minute_bars[(code, trade_date)]
			for code in codes
			if (code, trade_date) in self._five_minute_bars
		}
