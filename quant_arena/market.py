"""Baostock-backed market data service."""

from datetime import date, timedelta
from pathlib import Path
from logging import getLogger

import baostock as bs
import pandas as pd

from quant_arena.clock import now_shanghai

logger = getLogger(__name__)

class MarketService:
    def __init__(self, market_data_root: Path):
        self.market_data_root = market_data_root
        self.market_bars_dir = market_data_root / "bars"
        self._code_names_path = market_data_root / "code_names.csv"
        self._code_names: pd.DataFrame | None = None
        self.market_bars_dir.mkdir(parents=True, exist_ok=True)
        bs.login()

    def get_code_names(self) -> pd.DataFrame | None:
        """Return a data frame with columns `code`, `tradeStatus` and `code_name`."""
        if self._code_names is None and self._code_names_path.exists():
            self._code_names = pd.read_csv(self._code_names_path)
        return self._code_names

    def refresh_code_names(self) -> None:
        today = now_shanghai().date()
        with self._baostock_session():
            for offset in range(8):
                result = bs.query_all_stock((today - timedelta(days=offset)).isoformat())
                if result.error_code != "0":
                    raise RuntimeError(f"baostock all-stock query failed: {result.error_msg}")
                # frame = result.get_data()[["code", "code_name"]].rename(columns={"code_name": "name"})
                frame = result.get_data()
                if not frame.empty:
                    frame.to_csv(self._code_names_path, index=False)
                    self._code_names = frame
                    return

    def get_daily_bars(self, day: date) -> pd.DataFrame | None:
        path = self.market_bars_dir / day.isoformat() / "daily.csv"
        if path.exists():
            return pd.read_csv(path)
        return None

    def get_five_minute_bars(self, day: date) -> pd.DataFrame | None:
        date_dir = self.market_bars_dir / day.isoformat() / "5min"
        if not date_dir.exists():
            return None
        frame = pd.DataFrame()
        for path in sorted(date_dir.glob("*.csv")):
            frame = pd.concat(
                [frame, pd.read_csv(path)],
                ignore_index=True
            )
        return frame

    def fetch_daily_bar(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        frame = pd.DataFrame()
        result = bs.query_history_k_data_plus(
            code,
            "date,code,open,high,low,close,preclose,volume,amount",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            frequency="d",
            adjustflag="3"
        )
        if result.error_code != "0":
            raise RuntimeError(f"baostock daily-bar query failed: {result.error_msg}")
        frame = pd.concat([frame, result.get_data()], ignore_index=True)
        return frame

    def fetch_five_minute_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        frame = pd.DataFrame()
        result = bs.query_history_k_data_plus(
            code,
            "date,time,code,open,high,low,close,volume,amount",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            frequency="5",
            adjustflag="3"
        )
        if result.error_code != "0":
            raise RuntimeError(f"baostock five-minute query failed: {result.error_msg}")
        frame = pd.concat([frame, result.get_data()], ignore_index=True)
        return frame

    def persist_daily_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        for day_iso, date_frame in frame.groupby("date"):
            path = self.market_bars_dir / day_iso / "daily.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = pd.read_csv(path) if path.exists() else pd.DataFrame()
            merged = pd.concat([existing, date_frame], ignore_index=True)
            merged.drop_duplicates("code", keep="last").sort_values("code")
            merged.to_csv(path, index=False)

    def persist_five_minute_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        # time is something like 20260409093500000
        minutes = pd.to_datetime(frame["time"], format="%Y%m%d%H%M%S%f")
        writable = frame.assign(minute=minutes.dt.strftime("%H-%M"))
        for (day_iso, minute), minute_frame in writable.groupby(["date", "minute"]):
            path = self.market_bars_dir / day_iso / "5min" / f"{minute}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = pd.read_csv(path) if path.exists() else pd.DataFrame()
            merged = pd.concat([existing, minute_frame.drop(columns="minute")], ignore_index=True) if existing is not None else minute_frame.drop(columns="minute")
            merged.drop_duplicates("code", keep="last").sort_values("code")
            merged.to_csv(path, index=False)

    def sync_live_five_minute_bars(
        self,
        tracked_codes: set[str],
        today: date | None = None,
    ) -> pd.DataFrame:
        """When market is open, sync data and return latest 5min bars."""
        today = today or now_shanghai().date()
        frame = pd.DataFrame()
        for code in tracked_codes:
            code_frame = self.fetch_five_minute_bars(code, start_date=today, end_date=today)
            frame = pd.concat([frame, code_frame], ignore_index=True)
        self.persist_five_minute_frame(frame)
        return frame.drop_duplicates("code", keep="last")

    def finalize_market_data_after_market_closed(
        self,
        today: date | None = None,
        update_every: int = 500,
    ) -> None:
        """Invoke this after 5PM (baostock daily release time) to update daily bars."""
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
        return
