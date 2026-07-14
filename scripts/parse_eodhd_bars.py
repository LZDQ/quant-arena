#!/usr/bin/env python3
"""Manual EODHD daily-bulk or intraday-symbol persistence."""

import argparse
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from quant_arena.config import (
    EODHDExchangeConfig,
    load_app_config,
)
from quant_arena.eodhd import EODHDService
from quant_arena.server import DEFAULT_CONFIG_PATH


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _ensure_separate_market_data_roots(ashare_root: Path, eodhd_root: Path) -> None:
    if (
        ashare_root == eodhd_root
        or _path_is_relative_to(ashare_root, eodhd_root)
        or _path_is_relative_to(eodhd_root, ashare_root)
    ):
        raise SystemExit(
            "EODHD market-data output must be separate from the A-share baostock "
            f"directory. Got A-share={ashare_root} and EODHD={eodhd_root}."
        )


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
    today = datetime.now(timezone.utc).date()
    return today, today


def _resolve_exchanges(
    configured: dict[str, EODHDExchangeConfig],
    exchanges: list[str] | None,
) -> dict[str, EODHDExchangeConfig]:
    if exchanges is None:
        return configured
    resolved: dict[str, EODHDExchangeConfig] = {}
    for exchange in exchanges:
        value = exchange.strip().upper()
        if not value or value in resolved:
            continue
        resolved[value] = EODHDExchangeConfig(enabled=True)
    if not resolved:
        raise SystemExit("At least one --exchange value must be non-empty")
    return resolved


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(
        description=(
            "Persist EODHD daily bulk bars or 5min intraday bars for a date range. "
            "Run daily and 5min separately because EODHD exposes different API shapes."
        )
    )
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--date", type=date.fromisoformat, help="Persist a single UTC date.")
    parser.add_argument("--today", action="store_true", help="Persist today's UTC date.")
    parser.add_argument("--bars", choices=["daily", "5min"], required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to quant-arena config.json (defaults to ~/.quant-arena/config.json)",
    )
    parser.add_argument(
        "--market-data-dir",
        type=Path,
        default=None,
        help="Override the resolved EODHD market-data directory.",
    )
    parser.add_argument(
        "--api-token",
        type=str,
        default=None,
        help="Override config's eodhd.api_token.",
    )
    parser.add_argument(
        "--exchange",
        action="append",
        dest="exchanges",
        default=None,
        help="Override EODHD exchange list. Repeat for multiple exchanges.",
    )
    parser.add_argument("--persist-every", type=int, default=100)
    parser.add_argument("--verbose", action="store_true",
                        help="Log each fetched symbol and bar kind at INFO level.")
    args = parser.parse_args()

    start_date, end_date = _resolve_dates(args)
    config = load_app_config(args.config.resolve())
    market_data_dir = args.market_data_dir or config.eodhd.resolve_market_data_root(
        config.market_data_root
    )
    market_data_root = market_data_dir.resolve()
    _ensure_separate_market_data_roots(
        config.ashare.resolve_market_data_root(config.market_data_root),
        market_data_root,
    )
    market = EODHDService(
        api_token=args.api_token or config.eodhd.api_token,
        market_data_root=market_data_root,
        exchanges=_resolve_exchanges(
            config.eodhd.exchanges,
            args.exchanges,
        ),
        websocket_subscribe_limit=config.eodhd.websocket_subscribe_limit,
    )
    if args.bars == "daily":
        rows = market.persist_daily_history(
            start_date,
            end_date,
            overwrite=args.overwrite,
            show_progress=True,
            verbose=args.verbose,
            exchanges=args.exchanges,
        )
    else:
        rows = market.persist_intraday_history(
            start_date,
            end_date,
            overwrite=args.overwrite,
            persist_every=args.persist_every,
            show_progress=True,
            verbose=args.verbose,
            exchanges=args.exchanges,
        )
    logging.info(
        "Finished EODHD %s persistence for [%s, %s] (rows=%d)",
        args.bars,
        start_date,
        end_date,
        rows,
    )


if __name__ == "__main__":
    main()
