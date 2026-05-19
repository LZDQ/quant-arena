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

import asyncio
import math
import shutil
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from importlib import resources
from logging import getLogger
from pathlib import Path

import akshare as ak
import baostock as bs
import pandas as pd
import requests
from tqdm import tqdm

from quant_arena.clock import now_shanghai
from quant_arena.models import CorporateAction

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
        self._code_name_index: dict[str, str] | None = None
        self._latest_daily_frame: pd.DataFrame | None = None
        self._today_is_trading_day: tuple[date, bool] | None = None
        self.market_bars_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            resources.files("quant_arena.resources").joinpath("README-market-data.md"),
            market_data_root / "README.md",
        )

    def get_code_names(self) -> pd.DataFrame | None:
        if self._code_names is None and self._code_names_path.exists():
            self._code_names = self._read_csv(self._code_names_path)
        return self._code_names

    def get_code_name(self, code: str) -> str | None:
        """Return the cached display name for `code`, or None if not tracked."""
        if self._code_name_index is None:
            frame = self.get_code_names()
            if frame is None or frame.empty:
                self._code_name_index = {}
            else:
                self._code_name_index = dict(
                    zip(frame["code"].astype(str), frame["name"].astype(str))
                )
        return self._code_name_index.get(code)

    def refresh_code_names(self) -> None:
        frame = ak.stock_info_a_code_name()
        if frame.empty:
            raise ValueError("akshare returned empty code-name frame")
        frame.to_csv(self._code_names_path, index=False)
        self._code_names = frame
        self._code_name_index = None

    def get_latest_daily_bar(self) -> pd.DataFrame | None:
        """Return (and cache) the most recent persisted daily-bar frame."""
        if self._latest_daily_frame is not None:
            return self._latest_daily_frame
        for day_dir in sorted(
            (p for p in self.market_bars_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        ):
            path = day_dir / "daily.csv"
            if path.exists():
                frame = self._read_csv(path)
                if not frame.empty:
                    self._latest_daily_frame = frame
                    return self._latest_daily_frame
        return None

    def _fetch_daily_bar(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        return self._fetch_baostock_bars(code, start_date, end_date, "d")

    def _fetch_five_minute_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        return self._fetch_baostock_bars(code, start_date, end_date, "5")

    def fetch_price_limits(self, code: str) -> tuple[float, float, float]:
        """
        Return (limit_down, limit_up, prev_close) for `code` from EM.

        Single attempt against `stock_bid_ask_em` — no retries, EM is too
        unstable to wait on. Callers must catch and fall back when this raises.
        """
        frame = ak.stock_bid_ask_em(symbol=code)
        if frame is None or frame.empty:
            raise RuntimeError(f"stock_bid_ask_em returned empty frame for {code}")
        lookup = dict(zip(frame["item"].astype(str), frame["value"]))
        return float(lookup["跌停"]), float(lookup["涨停"]), float(lookup["昨收"])

    def fetch_intraday(
        self, code: str, today: date | None = None, page: int | None = None
    ) -> pd.DataFrame:
        """
        Live intraday ticks from AKShare sina.

        When `page` is set, only pages starting from that page number are
        returned. Frame attrs include the current `last_page`.
        """
        now = now_shanghai()
        today = today or now.date()
        try:
            frame = self._akshare_stock_intraday_sina(
                symbol=ak.stock_a_code_to_symbol(code),
                day=today.strftime("%Y%m%d"),
                page=page,
            )
        except KeyError as e:  # akshare raises KeyError: 'ticktime' on non-trading days
            raise RuntimeError(f"Intraday query failed; non-trading day? now={now}") from e
        copied = frame.copy()
        copied.attrs = dict(frame.attrs)
        if not copied.empty:
            copied["code"] = code
        return copied

    def _akshare_stock_intraday_sina(
        self, symbol: str, day: str, page: int | None = None
    ) -> pd.DataFrame:
        """
        Local reimplementation of AKShare's Sina intraday fetch with paging.

        `page=None` returns the full day. Otherwise only pages starting from
        `page` are fetched. The returned frame attrs include `last_page`.
        """
        count_url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "CN_Bill.GetBillListCount"
        )
        params = {
            "symbol": symbol,
            "num": "60",
            "page": "1",
            "sort": "ticktime",
            "asc": "1",
            "volume": "0",
            "amount": "0",
            "type": "0",
            "day": "-".join([day[:4], day[4:6], day[6:]]),
        }
        headers = {
            "Referer": (
                "https://vip.stock.finance.sina.com.cn/quotes_service/view/"
                f"cn_bill.php?symbol={symbol}"
            ),
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36"
            ),
        }
        count_response = requests.get(url=count_url, params=params, headers=headers)
        total_page = math.ceil(int(count_response.json()) / 60)
        if total_page <= 0:
            empty = pd.DataFrame()
            empty.attrs["last_page"] = 0
            empty.attrs["start_page"] = 1
            return empty

        start_page = 1 if page is None else max(1, min(int(page), total_page))
        list_url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "CN_Bill.GetBillList"
        )
        frames: list[pd.DataFrame] = []
        for current_page in range(start_page, total_page + 1):
            params["page"] = str(current_page)
            page_response = requests.get(url=list_url, params=params, headers=headers)
            page_frame = pd.DataFrame(page_response.json())
            if not page_frame.empty:
                frames.append(page_frame)

        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        frame.attrs["last_page"] = total_page
        frame.attrs["start_page"] = start_page
        if frame.empty:
            return frame
        frame.sort_values(by=["ticktime"], inplace=True, ignore_index=True)
        frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
        frame["prev_price"] = pd.to_numeric(frame["prev_price"], errors="coerce")
        return frame

    def fetch_trade_dates(self, start_date: date | None, end_date: date | None) -> pd.DataFrame:
        result = bs.query_trade_dates(start_date, end_date)
        if result.error_code != "0":
            raise RuntimeError(f"baostock trade-dates query failed: {result.error_msg}")
        frame = self._result_to_frame(result)
        if frame.empty:
            raise RuntimeError(
                f"baostock returned no trade dates for [{start_date}, {end_date}]"
            )
        return frame

    def fetch_corporate_actions(
        self, ex_date: date, codes: Iterable[str]
    ) -> list[CorporateAction]:
        """
        Cash-dividend / bonus-share events from baostock whose ex-date is ``ex_date``.

        Queried per code via ``query_dividend_data(yearType="operate")`` keyed by the
        ex-date's *year* — baostock keys this table by the ex-rights ("operate") date,
        not the report period, so a 2025-fiscal-year payout distributed in 2026 lives
        under ``year="2026"``. Only ``codes`` are scanned (dividend entitlement only
        matters for held positions), keeping this to a handful of round-trips a day.

        Per-share figures (``dividCashPsBeforeTax`` etc.) are taken as-is; the
        after-tax cash column from baostock is ignored — callers compute the
        differentiated dividend tax themselves from holding periods.
        """
        bs.login()
        actions: list[CorporateAction] = []
        target = ex_date.isoformat()
        year = str(ex_date.year)
        for code in codes:
            try:
                bs_code = f"{ak.stock_a_code_to_symbol(code)[:2]}.{code}"
                result = bs.query_dividend_data(code=bs_code, year=year, yearType="operate")
                if result.error_code != "0":
                    logger.warning(
                        "baostock dividend query failed for %s: %s", code, result.error_msg
                    )
                    continue
                while result.next():
                    row = dict(zip(result.fields, result.get_row_data()))
                    if row.get("dividOperateDate", "") != target:
                        continue
                    cash_ps = self._to_float(row.get("dividCashPsBeforeTax"))
                    bonus_ps = self._to_float(row.get("dividStocksPs"))
                    reserve_ps = self._to_float(row.get("dividReserveToStockPs"))
                    if cash_ps <= 0.0 and bonus_ps <= 0.0 and reserve_ps <= 0.0:
                        continue
                    register_raw = str(row.get("dividRegistDate", "") or "").strip() or target
                    actions.append(
                        CorporateAction(
                            code=code,
                            register_date=date.fromisoformat(register_raw),
                            ex_date=ex_date,
                            cash_per_share_pretax=cash_ps,
                            bonus_shares_per_share=bonus_ps,
                            reserve_shares_per_share=reserve_ps,
                            scheme=str(row.get("dividCashStock", "") or "").strip(),
                        )
                    )
            except Exception:
                logger.exception("Failed to fetch corporate actions for %s", code)
        return actions

    @staticmethod
    def _to_float(value: object) -> float:
        if value is None:
            return 0.0
        text = str(value).strip()
        if not text:
            return 0.0
        return float(text)

    def _persist_daily_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        frame = frame.assign(code=frame["code"].astype(str))
        for day_iso, sub in frame.groupby("date", sort=False):
            self._merge_and_write(self.market_bars_dir / str(day_iso) / "daily.csv", sub, ["code"])

    def _persist_five_minute_frame(self, frame: pd.DataFrame) -> None:
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
        verbose: bool = False,
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
            trade_frame.loc[trade_frame["is_trading_day"] == "1", "calendar_date"]
            .astype(str)
            .tolist()
        )
        if not dates:
            logger.warning(
                "No trading days in [%s, %s]; nothing to persist", start_date, end_date
            )
            return

        want_daily = bars in ("daily", "both")
        want_5min = bars in ("5min", "both")
        existing: dict[str, dict[str, set[str]]] = {"daily": {}, "5min": {}}
        if not overwrite:
            for day_iso in dates:
                day_dir = self.market_bars_dir / day_iso
                if want_daily:
                    daily_path = day_dir / "daily.csv"
                    if daily_path.exists():
                        df = self._read_csv(daily_path)
                        existing["daily"][day_iso] = set() if df.empty else set(df["code"].astype(str))
                    else:
                        existing["daily"][day_iso] = set()
                if want_5min:
                    five_min_path = day_dir / "5min.csv"
                    if five_min_path.exists():
                        df = self._read_csv(five_min_path)
                        existing["5min"][day_iso] = set() if df.empty else set(df["code"].astype(str))
                    else:
                        existing["5min"][day_iso] = set()

        buffers: dict[str, list[pd.DataFrame]] = {"daily": [], "5min": []}
        fetchers = {"daily": self._fetch_daily_bar, "5min": self._fetch_five_minute_bars}
        persisters = {"daily": self._persist_daily_frame, "5min": self._persist_five_minute_frame}
        kinds = [k for k in ("daily", "5min") if (want_daily if k == "daily" else want_5min)]
        fetched = skipped = 0

        progress = tqdm(codes, desc="Parsing bars", unit="code", disable=not show_progress)
        for code in progress:
            done = not overwrite and all(
                code in existing[kind][d] for kind in kinds for d in dates
            )
            if done:
                skipped += 1
            else:
                for kind in kinds:
                    df = fetchers[kind](code, start_date, end_date)
                    if not df.empty:
                        buffers[kind].append(df)
                    if verbose:
                        logger.info("Fetched %s bars for %s (rows=%d)", kind, code, len(df))
                fetched += 1
                if fetched % persist_every == 0:
                    for kind in kinds:
                        if buffers[kind]:
                            combined = pd.concat(buffers[kind], ignore_index=True, copy=False)
                            persisters[kind](combined)
                            if not overwrite:
                                for day_iso, sub in combined.groupby("date", sort=False):
                                    existing[kind][str(day_iso)].update(sub["code"].astype(str))
                            buffers[kind].clear()
            progress.set_postfix(skipped=skipped, fetched=fetched)

        for kind in kinds:
            if buffers[kind]:
                persisters[kind](pd.concat(buffers[kind], ignore_index=True, copy=False))

    def is_today_trading_day(self) -> bool:
        """Return today's trading-day flag, reusing the value cached by ``run()``."""
        today = now_shanghai().date()
        if self._today_is_trading_day is not None and self._today_is_trading_day[0] == today:
            return self._today_is_trading_day[1]
        bs.login()
        frame = self.fetch_trade_dates(today, today)
        flag = str(frame.iloc[-1]["is_trading_day"]) == "1"
        self._today_is_trading_day = (today, flag)
        return flag

    async def run(self, polling_interval_seconds: int) -> None:
        """
        Persist today's bars after the market closes.

        After 17:30, finalize today's daily bars using baostock.
        After 20:00, finalize today's 5min bars using baostock.
        Note that do not use multiple workers or restart the
        server frequently when finalizing.
        """
        bs.login()
        last_refreshed_date: date | None = None
        last_finalized_daily_date: date | None = None
        last_finalized_5min_date: date | None = None
        while True:
            now = now_shanghai()
            today = now.date()
            if last_refreshed_date != today:
                logger.debug("Refreshing today's trading status")
                last_refreshed_date = today
                try:
                    bs.login()
                    trade_date_frame = self.fetch_trade_dates(today, today)
                    is_trading_day = str(trade_date_frame.iloc[-1]["is_trading_day"]) == "1"
                    logger.info("Today's trading status is: %r", is_trading_day)
                except RuntimeError:
                    logger.exception("Cannot fetch today's trading status. Defaulting to False")
                    is_trading_day = False
                self._today_is_trading_day = (today, is_trading_day)

                if not is_trading_day:
                    tomorrow = datetime.combine(
                        today + timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo
                    )
                    await asyncio.sleep(max((tomorrow - now).total_seconds(), 0.0))
                    continue

                try:
                    await asyncio.to_thread(self.refresh_code_names)
                except Exception:
                    logger.exception("Failed to refresh code_names.csv")

            if now.time() >= time(17, 30) and last_finalized_daily_date != today:
                try:
                    await asyncio.to_thread(
                        self.persist_history,
                        today,
                        today,
                        "daily",
                        True,
                        500,
                    )
                    self._latest_daily_frame = None
                except Exception:
                    logger.exception("Exception in finalizing today's daily bars")
                last_finalized_daily_date = today

            if now.time() >= time(20, 0) and last_finalized_5min_date != today:
                try:
                    await asyncio.to_thread(
                        self.persist_history,
                        today,
                        today,
                        "5min",
                        True,
                        500,
                    )
                except Exception:
                    logger.exception("Exception in finalizing today's 5min bars")
                last_finalized_5min_date = today

            await asyncio.sleep(polling_interval_seconds)

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
