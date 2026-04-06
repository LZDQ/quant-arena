"""Market data provider interfaces."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Protocol

from quant_arena.models import QuoteSnapshot


class MarketDataProvider(Protocol):
	"""Protocol for market data providers."""

	def get_latest_quotes(self, symbols: list[str]) -> dict[str, QuoteSnapshot]:
		"""Return latest quotes for the requested symbols."""


class BaoStockMarketDataProvider:
	"""Thin baostock-backed provider."""

	def get_latest_quotes(self, symbols: list[str]) -> dict[str, QuoteSnapshot]:
		try:
			import baostock as bs
		except ImportError as exc:
			raise RuntimeError("baostock is not installed") from exc

		if not symbols:
			return {}

		login_result = bs.login()
		if getattr(login_result, "error_code", "0") != "0":
			raise RuntimeError(f"baostock login failed: {login_result.error_msg}")
		try:
			result = bs.query_stock_latest_info(",".join(symbols))
			if getattr(result, "error_code", "0") != "0":
				raise RuntimeError(f"baostock latest info query failed: {result.error_msg}")

			quotes: dict[str, QuoteSnapshot] = {}
			now = datetime.now(timezone.utc)
			while result.next():
				row = result.get_row_data()
				symbol = row[0]
				close = float(row[5])
				prev_close = float(row[6]) if row[6] else close
				limit_up = round(prev_close * 1.1, 2)
				limit_down = round(prev_close * 0.9, 2)
				quotes[symbol] = QuoteSnapshot(
					symbol=symbol,
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


class StaticMarketDataProvider:
	"""In-memory provider used by tests."""

	def __init__(self, quotes: dict[str, QuoteSnapshot]):
		self._quotes = quotes

	def get_latest_quotes(self, symbols: list[str]) -> dict[str, QuoteSnapshot]:
		return {symbol: self._quotes[symbol] for symbol in symbols if symbol in self._quotes}
