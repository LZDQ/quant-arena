from datetime import date

from quant_arena.models import CodeNameEntry, DailyBar, FiveMinuteBar, QuoteSnapshot


class StaticMarketDataProvider:
    """In-memory provider used by tests."""

    def __init__(
        self,
        quotes: dict[str, QuoteSnapshot],
        code_names: list[CodeNameEntry] | None = None,
        daily_bars: dict[tuple[str, date], DailyBar] | None = None,
        five_minute_bars: dict[tuple[str, date], list[FiveMinuteBar]] | None = None,
    ):
        self._quotes = quotes
        self._code_names = code_names or []
        self._daily_bars = daily_bars or {}
        self._five_minute_bars = five_minute_bars or {}
        self.daily_range_call_count = 0
        self.five_minute_range_call_count = 0

    def get_code_names(self, day: date) -> list[CodeNameEntry]:
        return list(self._code_names)

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

    def get_daily_bars_range(self, code: str, start_date: date, end_date: date) -> list[DailyBar]:
        self.daily_range_call_count += 1
        return [
            bar
            for (bar_code, trade_date), bar in sorted(self._daily_bars.items(), key=lambda item: item[0][1])
            if bar_code == code and start_date <= trade_date <= end_date
        ]

    def get_five_minute_bars_range(self, code: str, start_date: date, end_date: date) -> list[FiveMinuteBar]:
        self.five_minute_range_call_count += 1
        rows: list[FiveMinuteBar] = []
        for (bar_code, trade_date), bars in sorted(self._five_minute_bars.items(), key=lambda item: item[0][1]):
            if bar_code != code or not (start_date <= trade_date <= end_date):
                continue
            rows.extend(bars)
        return rows
