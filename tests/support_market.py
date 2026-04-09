from datetime import date

from quant_arena.models import CodeNameEntry, DailyBar, DataParserJobConfig, DataParserJobEntry, FiveMinuteBar, QuoteSnapshot


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
        self.history_parse_call_count = 0

    def get_code_names(self) -> list[CodeNameEntry]:
        return list(self._code_names)

    def refresh_code_names(self) -> None:
        return None

    def get_latest_quotes(self, codes: list[str]) -> dict[str, QuoteSnapshot]:
        return {code: self._quotes[code] for code in codes if code in self._quotes}

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

    def create_data_parser_job(self, config: DataParserJobConfig) -> DataParserJobEntry:
        self.history_parse_call_count += 1
        return DataParserJobEntry(
            config=config,
            skipped=0 if config.skip_existing else None,
            parsed=0,
            error=None,
            start_time=self._quotes[next(iter(self._quotes))].as_of,
            finish_time=self._quotes[next(iter(self._quotes))].as_of,
        )

    def list_data_parser_jobs(self) -> list[DataParserJobEntry]:
        return []
