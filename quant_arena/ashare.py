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

import shutil
from datetime import date
from importlib import resources
from logging import getLogger
from pathlib import Path

import akshare as ak
import baostock as bs
import pandas as pd
from tqdm import tqdm

from quant_arena.clock import now_shanghai

logger = getLogger(__name__)


class AShareService:
    """
    Mixed baostock + AKShare-sina A-share market data service.

    Persisted daily/5min bars come from baostock; intraday paper-trading
    quotes from AKShare sina. `persist_history` is the single entry point for
    backfilling/repairing bar files with skip-on-exists continuation built in.
    """

    def __init__(self, market_data_root: Path):
        self.market_data_root = market_data_root
        self.market_bars_dir = market_data_root / "bars"
        self._code_names_path = market_data_root / "code_names.csv"
        self._code_names: pd.DataFrame | None = None
        self.market_bars_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            resources.files("quant_arena.resources").joinpath("README-market-data.md"),
            market_data_root / "README.md",
        )
        bs.login()

    def get_code_names(self) -> pd.DataFrame | None:
        if self._code_names is None and self._code_names_path.exists():
            self._code_names = self._read_csv(self._code_names_path)
        return self._code_names

    def refresh_code_names(self) -> None:
        frame = ak.stock_info_a_code_name()
        if frame.empty:
            raise ValueError("akshare returned empty code-name frame")
        frame.to_csv(self._code_names_path, index=False)
        self._code_names = frame

    def get_daily_bars(self, day: date) -> pd.DataFrame | None:
        path = self.market_bars_dir / day.isoformat() / "daily.csv"
        return self._read_csv(path) if path.exists() else None

    def get_five_minute_bars(self, day: date) -> pd.DataFrame | None:
        path = self.market_bars_dir / day.isoformat() / "5min.csv"
        return self._read_csv(path) if path.exists() else None

    def get_latest_daily_bar(self) -> pd.DataFrame | None:
        for day_dir in sorted(
            (p for p in self.market_bars_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        ):
            path = day_dir / "daily.csv"
            if path.exists():
                frame = self._read_csv(path)
                if not frame.empty:
                    return frame
        return None

    def fetch_daily_bar(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        return self._fetch_baostock_bars(code, start_date, end_date, "d")

    def fetch_five_minute_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        return self._fetch_baostock_bars(code, start_date, end_date, "5")

    def fetch_intraday(self, code: str, today: date | None = None) -> pd.DataFrame:
        """Live intraday ticks from AKShare sina; columns include ticktime, price, volume, code."""
        now = now_shanghai()
        today = today or now.date()
        try:
            frame = ak.stock_intraday_sina(
                symbol=ak.stock_a_code_to_symbol(code),
                date=today.strftime("%Y%m%d"),
            )
        except KeyError as e:  # akshare raises KeyError: 'ticktime' on non-trading days
            raise RuntimeError(f"Intraday query failed; non-trading day? now={now}") from e
        if frame.empty:
            return frame
        frame = frame.copy()
        frame["code"] = code
        return frame

    def fetch_trade_dates(self, start_date: date | None, end_date: date | None) -> pd.DataFrame:
        result = bs.query_trade_dates(start_date, end_date)
        if result.error_code != "0":
            raise RuntimeError(f"baostock trade-dates query failed: {result.error_msg}")
        return self._result_to_frame(result)

    def persist_daily_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        frame = frame.assign(code=frame["code"].astype(str))
        for day_iso, sub in frame.groupby("date", sort=False):
            self._merge_and_write(self.market_bars_dir / str(day_iso) / "daily.csv", sub, ["code"])

    def persist_five_minute_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        frame = frame.assign(code=frame["code"].astype(str))
        for day_iso, sub in frame.groupby("date", sort=False):
            self._merge_and_write(
                self.market_bars_dir / str(day_iso) / "5min.csv",
                sub,
                ["code", "time"],
                sort_keys=["time", "code"],
            )

    def persist_history(
        self,
        start_date: date,
        end_date: date,
        bars: str = "both",
        overwrite: bool = False,
        persist_every: int = 100,
        show_progress: bool = False,
    ) -> None:
        """
        Backfill / repair persisted daily and/or 5-min bars over a date range.

        Skip-on-exists: when `overwrite=False`, codes that already have rows in
        every requested (date, bar-kind) target file are skipped. Buffers flush
        every `persist_every` newly fetched codes; the existing-code index is
        updated in place after each flush.

        Buffers are `list[DataFrame]` and concat'd once per flush — the original
        quadratic `pd.concat([acc, new])` per code is gone.
        """
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        if persist_every <= 0:
            raise ValueError("persist_every must be positive")

        bs.login()
        code_table = self.get_code_names()
        if code_table is None or code_table.empty:
            raise ValueError("No code names tracked. Call refresh_code_names() first.")
        codes = code_table["code"].astype(str).tolist()

        trade_frame = self.fetch_trade_dates(start_date, end_date)
        dates: list[str] = (
            []
            if trade_frame.empty
            else trade_frame.loc[trade_frame["is_trading_day"] == "1", "calendar_date"].astype(str).tolist()
        )

        want_daily = bars in ("daily", "both")
        want_5min = bars in ("5min", "both")
        existing: dict[str, dict[str, set[str]]] = {"daily": {}, "5min": {}}
        if not overwrite:
            for day_iso in dates:
                day = date.fromisoformat(day_iso)
                if want_daily:
                    df = self.get_daily_bars(day)
                    existing["daily"][day_iso] = set() if df is None or df.empty else set(df["code"].astype(str))
                if want_5min:
                    df = self.get_five_minute_bars(day)
                    existing["5min"][day_iso] = set() if df is None or df.empty else set(df["code"].astype(str))

        buffers: dict[str, list[pd.DataFrame]] = {"daily": [], "5min": []}
        fetchers = {"daily": self.fetch_daily_bar, "5min": self.fetch_five_minute_bars}
        persisters = {"daily": self.persist_daily_frame, "5min": self.persist_five_minute_frame}
        kinds = [k for k in ("daily", "5min") if (want_daily if k == "daily" else want_5min)]
        fetched = skipped = 0

        progress = tqdm(codes, desc="Parsing bars", unit="code", disable=not show_progress)
        for code in progress:
            done = not overwrite and dates and all(
                code in existing[kind].get(d, ()) for kind in kinds for d in dates
            )
            if done:
                skipped += 1
            else:
                for kind in kinds:
                    df = fetchers[kind](code, start_date, end_date)
                    if not df.empty:
                        buffers[kind].append(df)
                fetched += 1
                if fetched % persist_every == 0:
                    for kind in kinds:
                        if buffers[kind]:
                            combined = pd.concat(buffers[kind], ignore_index=True, copy=False)
                            persisters[kind](combined)
                            for day_iso, sub in combined.groupby("date", sort=False):
                                existing[kind].setdefault(str(day_iso), set()).update(sub["code"].astype(str))
                            buffers[kind].clear()
            progress.set_postfix(skipped=skipped, fetched=fetched)

        for kind in kinds:
            if buffers[kind]:
                persisters[kind](pd.concat(buffers[kind], ignore_index=True, copy=False))

    def finalize_market_data_daily(self, today: date | None = None) -> None:
        """Finalize today's daily bars (baostock release: after 17:30)."""
        today = today or now_shanghai().date()
        self.persist_history(today, today, bars="daily", overwrite=True, persist_every=500)

    def finalize_market_data_5min(self, today: date | None = None) -> None:
        """Finalize today's 5-min bars (baostock release: after 20:00)."""
        today = today or now_shanghai().date()
        self.persist_history(today, today, bars="5min", overwrite=True, persist_every=500)

    def _fetch_baostock_bars(self, code: str, start_date: date, end_date: date, frequency: str) -> pd.DataFrame:
        fields = (
            "date,code,open,high,low,close,preclose,volume,amount"
            if frequency == "d"
            else "date,time,code,open,high,low,close,volume,amount"
        )
        result = bs.query_history_k_data_plus(
            f"{ak.stock_a_code_to_symbol(code)[:2]}.{code}",
            fields,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            frequency=frequency,
            adjustflag="3",
        )
        if result.error_code != "0":
            raise RuntimeError(f"baostock {frequency}-bar query failed: {result.error_msg}")
        frame = self._result_to_frame(result)
        if not frame.empty:
            frame["code"] = str(code)  # baostock returns "sh.600000"; normalize to "600000"
        return frame

    def _merge_and_write(
        self,
        path: Path,
        new_rows: pd.DataFrame,
        dedupe_keys: list[str],
        sort_keys: list[str] | None = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = (
            pd.concat([self._read_csv(path), new_rows], ignore_index=True, copy=False)
            if path.exists()
            else new_rows
        )
        merged["code"] = merged["code"].astype(str)
        merged = merged.drop_duplicates(dedupe_keys, keep="last").sort_values(sort_keys or dedupe_keys)
        merged.to_csv(path, index=False)

    @staticmethod
    def _result_to_frame(result: object) -> pd.DataFrame:
        rows: list[list[str]] = []
        while result.error_code == "0" and result.next():
            rows.append(result.get_row_data())
        return pd.DataFrame(rows, columns=result.fields)

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        return pd.read_csv(path, dtype={"code": str})
