#!/usr/bin/env python3

import argparse
from datetime import date
from pathlib import Path
from logging import getLogger

import pandas as pd
from tqdm import tqdm
import logging

from quant_arena.market import MarketService


DEFAULT_MARKET_DATA_DIR = Path.home() / ".quant-arena" / "market-data"
logger = getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse and persist daily / 5min bars for a date range.")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--bars", choices=["daily", "5min", "both"], default="both")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--market-data-dir", type=Path, default=DEFAULT_MARKET_DATA_DIR)
    parser.add_argument("--persist-every", type=int, default=100)
    return parser.parse_args()


def trading_dates(market: MarketService, start_date: date, end_date: date) -> list[str]:
    frame = market.fetch_trade_dates(start_date, end_date)
    if frame.empty:
        return []
    return list(frame.loc[frame["is_trading_day"] == "1", "calendar_date"].astype(str))


def build_existing_index(
    market: MarketService,
    dates: list[str],
    bars: str,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    daily_codes_by_date: dict[str, set[str]] = {}
    five_minute_codes_by_date: dict[str, set[str]] = {}

    for day_iso in dates:
        day = date.fromisoformat(day_iso)
        if bars in {"daily", "both"}:
            frame = market.get_daily_bars(day)
            daily_codes_by_date[day_iso] = set() if frame is None or frame.empty else set(frame["code"].astype(str))
        if bars in {"5min", "both"}:
            frame = market.get_five_minute_bars(day)
            five_minute_codes_by_date[day_iso] = set() if frame is None or frame.empty else set(frame["code"].astype(str))

    return daily_codes_by_date, five_minute_codes_by_date


def has_complete_data(
    code: str,
    dates: list[str],
    bars: str,
    daily_codes_by_date: dict[str, set[str]],
    five_minute_codes_by_date: dict[str, set[str]],
) -> bool:
    if not dates:
        return True
    if bars == "daily":
        return all(code in daily_codes_by_date[day_iso] for day_iso in dates)
    if bars == "5min":
        return all(code in five_minute_codes_by_date[day_iso] for day_iso in dates)
    return all(code in daily_codes_by_date[day_iso] for day_iso in dates) and all(
        code in five_minute_codes_by_date[day_iso] for day_iso in dates
    )


def update_existing_index(
    frame: pd.DataFrame,
    codes_by_date: dict[str, set[str]],
) -> None:
    if frame.empty:
        return
    for day_iso, date_frame in frame.groupby("date"):
        if day_iso not in codes_by_date:
            codes_by_date[day_iso] = set()
        codes_by_date[day_iso].update(date_frame["code"].astype(str))


def flush_frames(
    market: MarketService,
    daily_frame: pd.DataFrame,
    five_minute_frame: pd.DataFrame,
    bars: str,
    daily_codes_by_date: dict[str, set[str]],
    five_minute_codes_by_date: dict[str, set[str]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if bars in {"daily", "both"} and not daily_frame.empty:
        market.persist_daily_frame(daily_frame)
        update_existing_index(daily_frame, daily_codes_by_date)
        daily_frame = pd.DataFrame()
    if bars in {"5min", "both"} and not five_minute_frame.empty:
        market.persist_five_minute_frame(five_minute_frame)
        update_existing_index(five_minute_frame, five_minute_codes_by_date)
        five_minute_frame = pd.DataFrame()
    return daily_frame, five_minute_frame


def main() -> None:
    args = parse_args()
    if args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date")
    if args.persist_every <= 0:
        raise SystemExit("--persist-every must be positive")

    market = MarketService(args.market_data_dir)
    code_names = market.get_code_names()
    if code_names is None:
        market.refresh_code_names()
        code_names = market.get_code_names()
    if code_names is None or code_names.empty:
        raise SystemExit("No code names available. Failed to load market code table.")

    dates = trading_dates(market, args.start_date, args.end_date)
    codes = list(code_names["code"].astype(str))
    daily_codes_by_date: dict[str, set[str]] = {}
    five_minute_codes_by_date: dict[str, set[str]] = {}
    if not args.overwrite:
        daily_codes_by_date, five_minute_codes_by_date = build_existing_index(market, dates, args.bars)
        daily_file_count = sum(1 for codes_on_day in daily_codes_by_date.values() if codes_on_day)
        five_minute_file_count = sum(1 for codes_on_day in five_minute_codes_by_date.values() if codes_on_day)
        logger.info(
            "Loaded existing bar index: trade_dates=%d bars=%s daily_files=%d five_minute_files=%d",
            len(dates),
            args.bars,
            daily_file_count,
            five_minute_file_count,
        )
    else:
        logger.info("Overwrite enabled: skipping existing bar index for %d trade dates", len(dates))
    daily_frame = pd.DataFrame()
    five_minute_frame = pd.DataFrame()
    fetched_codes = 0
    skipped_codes = 0

    progress = tqdm(codes, desc="Parsing bars", unit="code")
    for code in progress:
        if not args.overwrite and has_complete_data(
            code,
            dates,
            args.bars,
            daily_codes_by_date,
            five_minute_codes_by_date,
        ):
            skipped_codes += 1
            progress.set_postfix(skipped=skipped_codes, fetched=fetched_codes)
            continue

        if args.bars in {"daily", "both"}:
            code_daily = market.fetch_daily_bar(code, args.start_date, args.end_date)
            if not code_daily.empty:
                daily_frame = pd.concat([daily_frame, code_daily], ignore_index=True)

        if args.bars in {"5min", "both"}:
            code_five_minute = market.fetch_five_minute_bars(code, args.start_date, args.end_date)
            if not code_five_minute.empty:
                five_minute_frame = pd.concat([five_minute_frame, code_five_minute], ignore_index=True)

        fetched_codes += 1
        progress.set_postfix(skipped=skipped_codes, fetched=fetched_codes)
        if fetched_codes % args.persist_every == 0:
            daily_frame, five_minute_frame = flush_frames(
                market,
                daily_frame,
                five_minute_frame,
                args.bars,
                daily_codes_by_date,
                five_minute_codes_by_date,
            )

    flush_frames(
        market,
        daily_frame,
        five_minute_frame,
        args.bars,
        daily_codes_by_date,
        five_minute_codes_by_date,
    )
    print(
        f"Done. trade_dates={len(dates)} total_codes={len(codes)} "
        f"fetched_codes={fetched_codes} skipped_codes={skipped_codes}"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
