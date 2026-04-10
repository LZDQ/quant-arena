"""
Baostock-backed market data service.

Stock endpoints summary:
- `baostock` cannot get live intraday data
- `ak.stock_intraday_em` not stable
- `ak.stock_intraday_sina` is stable for today's live data
- `ak.stock_zh_a_daily` is stable
- `ak.stock_zh_a_minute` is limited to latest 10 days

Final choices:
- `baostock` for after-market finalization every day
- `ak.stock_intraday_sina` for live data and paper trading
"""

from logging import getLogger
from datetime import date
from pathlib import Path

import akshare as ak
import baostock as bs
import pandas as pd

from quant_arena.clock import now_shanghai

logger = getLogger(__name__)

class MarketService:
    """
    A mix of baostock and AKShare sina market data service.

    Persisted data, including daily bars and 5min bars, is provided by baostock.
    Intraday data for paper trading is provided by AKShare sina.
    """
    def __init__(self, market_data_root: Path):
        self.market_data_root = market_data_root
        self.market_bars_dir = market_data_root / "bars"
        self._code_names_path = market_data_root / "code_names.csv"
        self._code_names: pd.DataFrame | None = None
        self.market_bars_dir.mkdir(parents=True, exist_ok=True)
        bs.login()

    def get_code_names(self) -> pd.DataFrame | None:
        """Return the raw AKShare code-name table."""
        if self._code_names is None and self._code_names_path.exists():
            self._code_names = self._read_csv(self._code_names_path)
        return self._code_names

    def refresh_code_names(self) -> None:
        frame = ak.stock_info_a_code_name()
        if not frame.empty:
            frame.to_csv(self._code_names_path, index=False)
            self._code_names = frame
        else:
            raise ValueError("Failed to refresh code names: akshare returned empty data frame")

    def get_daily_bars(self, day: date) -> pd.DataFrame | None:
        path = self.market_bars_dir / day.isoformat() / "daily.csv"
        if path.exists():
            return self._read_csv(path)
        return None

    def get_latest_daily_bar(self) -> pd.DataFrame | None:
        """Return the latest daily bar. Useful for calculating daily limit."""
        for day_dir in sorted(
            (path for path in self.market_bars_dir.iterdir() if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        ):
            path = day_dir / "daily.csv"
            if not path.exists():
                continue
            frame = self._read_csv(path)
            if frame.empty:
                continue
            return frame

        return None

    def get_five_minute_bars(self, day: date) -> pd.DataFrame | None:
        path = self.market_bars_dir / day.isoformat() / "5min.csv"
        if not path.exists():
            return None
        return self._read_csv(path)

    def fetch_daily_bar(
        self,
        code: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Fetch persisted, stable history daily bar from baostock."""
        logger.debug("Fetching baostock daily bar for %s from %s to %s",
                     code, start_date, end_date)
        result = bs.query_history_k_data_plus(
            f"{ak.stock_a_code_to_symbol(code)[:2]}.{code}",
            "date,code,open,high,low,close,preclose,volume,amount",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            frequency="d",
            adjustflag="3",
        )
        if result.error_code != "0":
            raise RuntimeError(f"baostock daily-bar query failed: {result.error_msg}")
        frame = self._result_to_frame(result)
        if not frame.empty:
            frame = frame.copy()
            frame["code"] = str(code)  # overwrite baostock format code back to ours without sh. or sz. prefix
        return frame

    def fetch_five_minute_bars(
        self,
        code: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        result = bs.query_history_k_data_plus(
            f"{ak.stock_a_code_to_symbol(code)[:2]}.{code}",
            "date,time,code,open,high,low,close,volume,amount",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            frequency="5",
            adjustflag="3",
        )
        if result.error_code != "0":
            raise RuntimeError(f"baostock five-minute query failed: {result.error_msg}")
        frame = self._result_to_frame(result)
        if not frame.empty:
            frame = frame.copy()
            frame["code"] = str(code)  # overwrite baostock format code back to ours without sh. or sz. prefix
        return frame

    def persist_daily_frame(self, frame: pd.DataFrame) -> None:
        """Given daily bar frame, combine on-disk data and persist back to disk."""
        if frame.empty:
            return
        for day_iso, date_frame in frame.groupby("date"):
            path = self.market_bars_dir / day_iso / "daily.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = self._read_csv(path) if path.exists() else pd.DataFrame()
            writable = date_frame.copy()
            writable["code"] = writable["code"].astype(str)
            merged = pd.concat([existing, writable], ignore_index=True)
            merged["code"] = merged["code"].astype(str)
            merged = merged.drop_duplicates("code", keep="last").sort_values("code")
            merged.to_csv(path, index=False)

    def persist_five_minute_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        for day_iso, date_frame in frame.groupby("date"):
            path = self.market_bars_dir / day_iso / "5min.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = self._read_csv(path) if path.exists() else pd.DataFrame()
            writable = date_frame.copy()
            writable["code"] = writable["code"].astype(str)
            merged = pd.concat([existing, writable], ignore_index=True)
            merged["code"] = merged["code"].astype(str)
            merged = merged.drop_duplicates(["code", "time"], keep="last").sort_values(["time", "code"])
            merged.to_csv(path, index=False)

    def refresh_intraday(
        self,
        tracked_codes: set[str],
        today: date | None = None,
    ) -> pd.DataFrame:
        """
        When market is open, refresh and return intraday data in-memory.

        Columns:
            symbol    name      ticktime  price  volume  prev_price kind
            sz000001  平安银行  09:25:00  11.10  300400        0.00    U
            sz000001  平安银行  09:30:00  11.09   39100       11.10    D
            sz000001  平安银行  09:30:03  11.10  325600       11.09    U

        Column `code` will also be injected into the result.
        """
        today = today or now_shanghai().date()

        frame = pd.DataFrame()
        for code in tracked_codes:
            intraday = ak.stock_intraday_sina(
                symbol=ak.stock_a_code_to_symbol(code),
                date=today.strftime("%Y%m%d"),
            )
            if intraday.empty:
                continue
            intraday = intraday.copy()
            intraday["code"] = code
            frame = pd.concat([frame, intraday], ignore_index=True)

        return frame

    def finalize_market_data_after_market_closed(
        self,
        today: date | None = None,
        update_every: int = 500,
    ) -> None:
        """
        Invoke this after market close to update persisted daily and 5-minute bars from baostock.

        baostock release time every day: after 8PM
        """
        logger.info("Start finalizing today's bar")
        today = today or now_shanghai().date()
        daily_frame = pd.DataFrame()
        five_minute_frame = pd.DataFrame()
        code_names = self.get_code_names()
        if code_names is None:
            raise ValueError("No code names tracked")
        for i, code in enumerate(code_names['code'], start=1):
            daily_frame = pd.concat(
                [daily_frame, self.fetch_daily_bar(code, today, today)],
                ignore_index=True,
            )
            five_minute_frame = pd.concat(
                [five_minute_frame, self.fetch_five_minute_bars(code, today, today)],
                ignore_index=True,
            )
            if i % update_every == 0 or i == len(code_names):
                logger.info("Finalization progress: %d/%d", i, len(code_names))
                self.persist_daily_frame(daily_frame)
                self.persist_five_minute_frame(five_minute_frame)
                daily_frame = pd.DataFrame()
                five_minute_frame = pd.DataFrame()
        return

    def fetch_trade_dates(
        self,
        start_date: date | None,
        end_date: date | None
    ) -> pd.DataFrame:
        """
        Fetch whether each day is trading day (thin wrapper for `bs.query_trade_dates`).

        Example:
            calendar_date   is_trading_day
            2026-04-09              1
            2026-04-10              1
        """
        result = bs.query_trade_dates(start_date, end_date)
        if result.error_code != "0":
            raise RuntimeError(f"baostock trade-dates query failed: {result.error_msg}")
        return self._result_to_frame(result)

    @staticmethod
    def _result_to_frame(result: object) -> pd.DataFrame:
        rows: list[list[str]] = []
        while result.error_code == "0" and result.next():
            rows.append(result.get_row_data())
        return pd.DataFrame(rows, columns=result.fields)

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        return pd.read_csv(path, dtype={"code": str})
