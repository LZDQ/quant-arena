#!/usr/bin/env python3
"""Thin CLI wrapper over `AShareService.persist_history`."""

import argparse
import logging
from datetime import date
from pathlib import Path

from quant_arena.clock import now_shanghai
from quant_arena.config import load_app_config
from quant_arena.ashare import AShareService
from quant_arena.server import DEFAULT_CONFIG_PATH


def _resolve_dates(args: argparse.Namespace) -> tuple[date, date]:
    has_range = args.start_date is not None or args.end_date is not None
    has_single = args.date is not None
    has_today = args.today
    chosen = sum((has_range, has_single, has_today))
    if chosen != 1:
        raise SystemExit("Specify exactly one of: (--start-date AND --end-date) | --date | --today")
    if has_range:
        if args.start_date is None or args.end_date is None:
            raise SystemExit("--start-date and --end-date must be provided together")
        if args.end_date < args.start_date:
            raise SystemExit("--end-date must be on or after --start-date")
        return args.start_date, args.end_date
    if has_single:
        return args.date, args.date
    today = now_shanghai().date()
    return today, today


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Persist daily / 5min bars for a date range.")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--date", type=date.fromisoformat, help="Persist a single date.")
    parser.add_argument("--today", action="store_true", help="Persist today's Shanghai trade date.")
    parser.add_argument("--bars", choices=["daily", "5min", "both"], default="both")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help="Path to quant-arena config.json (defaults to ~/.quant-arena/config.json)")
    parser.add_argument("--market-data-dir", type=Path, default=None,
                        help="Override config's ashare.market_data_root.")
    parser.add_argument("--persist-every", type=int, default=100)
    parser.add_argument("--verbose", action="store_true",
                        help="Log each fetched code and bar kind at INFO level.")
    args = parser.parse_args()

    start_date, end_date = _resolve_dates(args)
    market_data_dir = args.market_data_dir or Path(load_app_config(args.config.resolve()).ashare.market_data_root)
    market = AShareService(market_data_dir.resolve())
    if market.get_code_names() is None:
        market.refresh_code_names()
    market.persist_history(
        start_date,
        end_date,
        bars=args.bars,
        overwrite=args.overwrite,
        persist_every=args.persist_every,
        show_progress=True,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
