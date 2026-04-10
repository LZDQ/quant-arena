#!/usr/bin/env python3

import argparse
from datetime import date
from pathlib import Path

import pandas as pd
from tqdm import tqdm
import logging

from quant_arena.market import MarketService


DEFAULT_MARKET_DATA_DIR = Path.home() / ".quant-arena" / "market-data"


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


def existing_daily_dates(market: MarketService, code: str, dates: list[str]) -> set[str]:
    existing: set[str] = set()
    for day_iso in dates:
        frame = market.get_daily_bars(date.fromisoformat(day_iso))
        if frame is None or frame.empty:
            continue
        if frame["code"].astype(str).eq(code).any():
            existing.add(day_iso)
    return existing


def existing_five_minute_dates(market: MarketService, code: str, dates: list[str]) -> set[str]:
    existing: set[str] = set()
    for day_iso in dates:
        frame = market.get_five_minute_bars(date.fromisoformat(day_iso))
        if frame is None or frame.empty:
            continue
        if frame["code"].astype(str).eq(code).any():
            existing.add(day_iso)
    return existing


def has_complete_data(
    market: MarketService,
    code: str,
    dates: list[str],
    bars: str,
) -> bool:
    if not dates:
        return True
    if bars == "daily":
        return existing_daily_dates(market, code, dates) == set(dates)
    if bars == "5min":
        return existing_five_minute_dates(market, code, dates) == set(dates)
    return (
        existing_daily_dates(market, code, dates) == set(dates)
        and existing_five_minute_dates(market, code, dates) == set(dates)
    )


def flush_frames(
    market: MarketService,
    daily_frame: pd.DataFrame,
    five_minute_frame: pd.DataFrame,
    bars: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if bars in {"daily", "both"} and not daily_frame.empty:
        market.persist_daily_frame(daily_frame)
        daily_frame = pd.DataFrame()
    if bars in {"5min", "both"} and not five_minute_frame.empty:
        market.persist_five_minute_frame(five_minute_frame)
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
    daily_frame = pd.DataFrame()
    five_minute_frame = pd.DataFrame()
    fetched_codes = 0
    skipped_codes = 0

    progress = tqdm(codes, desc="Parsing bars", unit="code")
    for code in progress:
        if not args.overwrite and has_complete_data(market, code, dates, args.bars):
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
            )

    flush_frames(market, daily_frame, five_minute_frame, args.bars)
    print(
        f"Done. trade_dates={len(dates)} total_codes={len(codes)} "
        f"fetched_codes={fetched_codes} skipped_codes={skipped_codes}"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
