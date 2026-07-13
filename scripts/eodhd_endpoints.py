"""Runnable demos for every EODHD endpoint used by quant-arena."""

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from eodhd import APIClient
from websockets.sync.client import connect

from quant_arena.config import load_app_config


CONFIG_PATH = Path.home() / ".quant-arena" / "config.json"
REST_BASE = "https://eodhd.com/api"
WS_BASE = "wss://ws.eodhistoricaldata.com/ws"


def exchange_trading_calendar(
    client: APIClient, api_token: str, exchange: str, day: str
) -> None:
    print("\nExchange trading hours and holidays")
    print("  SDK: get_details_trading_hours_stock_market_holidays(...)")
    result = client.get_details_trading_hours_stock_market_holidays(
        code=exchange,
        from_date=day,
        to_date=day,
    )
    print("  SDK result:", result)
    working_days = result["TradingHours"]["WorkingDays"].split(",")
    closed_dates = {
        holiday["Date"]
        for holiday in result["ExchangeHolidays"].values()
        if holiday["Type"].lower().replace("-", "") != "earlyclose"
    }
    print("  Is trading day:", date.fromisoformat(day).strftime("%a") in working_days and day not in closed_dates)

    url = f"{REST_BASE}/exchange-details/{exchange}?" + urlencode(
        {"api_token": api_token, "fmt": "json", "from": day, "to": day}
    )
    print(f"  URL: {REST_BASE}/exchange-details/{exchange}?api_token=<API_TOKEN>&fmt=json&from={day}&to={day}")
    with urlopen(url) as response:
        print("  URL result:", json.loads(response.read().decode("utf-8")))


def exchange_symbol_list(client: APIClient, api_token: str, exchange: str) -> None:
    print(f"\nExchange symbols\n  SDK: get_exchange_symbols(uri={exchange!r})")
    print("  SDK result:", client.get_exchange_symbols(uri=exchange, delisted=False)[:1])

    url = f"{REST_BASE}/exchange-symbol-list/{exchange}?" + urlencode(
        {"api_token": api_token, "fmt": "json"}
    )
    print(f"  URL: {REST_BASE}/exchange-symbol-list/{exchange}?api_token=<API_TOKEN>&fmt=json")
    with urlopen(url) as response:
        print("  URL result:", json.loads(response.read().decode("utf-8"))[:1])


def bulk_daily_eod(client: APIClient, api_token: str, exchange: str, day: str) -> None:
    print("\nBulk daily EOD")
    print(f"  SDK: get_eod_splits_dividends_data(country={exchange!r}, date={day!r})")
    result = client.get_eod_splits_dividends_data(country=exchange, date=day)
    print("  SDK result:", result[:1])

    url = f"{REST_BASE}/eod-bulk-last-day/{exchange}?" + urlencode(
        {"api_token": api_token, "fmt": "json", "date": day}
    )
    print(f"  URL: {REST_BASE}/eod-bulk-last-day/{exchange}?api_token=<API_TOKEN>&date={day}")
    with urlopen(url) as response:
        print("  URL result:", json.loads(response.read().decode("utf-8"))[:1])


def bulk_dividends(client: APIClient, api_token: str, exchange: str, day: str) -> None:
    print("\nBulk dividends")
    print("  SDK: get_eod_splits_dividends_data(..., type='dividends')")
    result = client.get_eod_splits_dividends_data(
        country=exchange, date=day, type="dividends"
    )
    print("  SDK result:", result[:1])

    url = f"{REST_BASE}/eod-bulk-last-day/{exchange}?" + urlencode(
        {"api_token": api_token, "fmt": "json", "date": day, "type": "dividends"}
    )
    print(f"  URL: {REST_BASE}/eod-bulk-last-day/{exchange}?api_token=<API_TOKEN>&date={day}&type=dividends")
    with urlopen(url) as response:
        print("  URL result:", json.loads(response.read().decode("utf-8"))[:1])


def bulk_splits(client: APIClient, api_token: str, exchange: str, day: str) -> None:
    print("\nBulk splits")
    print("  SDK: get_eod_splits_dividends_data(..., type='splits')")
    result = client.get_eod_splits_dividends_data(
        country=exchange, date=day, type="splits"
    )
    print("  SDK result:", result[:1])

    url = f"{REST_BASE}/eod-bulk-last-day/{exchange}?" + urlencode(
        {"api_token": api_token, "fmt": "json", "date": day, "type": "splits"}
    )
    print(f"  URL: {REST_BASE}/eod-bulk-last-day/{exchange}?api_token=<API_TOKEN>&date={day}&type=splits")
    with urlopen(url) as response:
        print("  URL result:", json.loads(response.read().decode("utf-8"))[:1])


def intraday_history(client: APIClient, api_token: str, symbol: str, day: date) -> None:
    start = str(int(datetime.combine(day, time.min, timezone.utc).timestamp()))
    end = str(int(datetime.combine(day, time.max, timezone.utc).timestamp()))
    print("\n5-minute intraday history")
    print(f"  SDK: get_intraday_historical_data(symbol={symbol!r}, interval='5m')")
    result = client.get_intraday_historical_data(
        symbol=symbol, interval="5m", from_unix_time=start, to_unix_time=end
    )
    print("  SDK result:", result[:1])

    url = f"{REST_BASE}/intraday/{symbol}?" + urlencode(
        {"api_token": api_token, "fmt": "json", "interval": "5m", "from": start, "to": end}
    )
    print(f"  URL: {REST_BASE}/intraday/{symbol}?api_token=<API_TOKEN>&interval=5m&from={start}&to={end}")
    with urlopen(url) as response:
        print("  URL result:", json.loads(response.read().decode("utf-8"))[:1])


def websocket_us_quote(api_token: str, symbol: str) -> None:
    print(f"\nUS live quote\n  URL: {WS_BASE}/us?api_token=<API_TOKEN>")
    with connect(f"{WS_BASE}/us?api_token={api_token}") as ws:
        ws.send(json.dumps({"action": "subscribe", "symbols": symbol}))
        ws.recv()
        print("  Result:", json.loads(ws.recv()))


def websocket_forex_quote(api_token: str, symbol: str) -> None:
    print(f"\nForex live quote\n  URL: {WS_BASE}/forex?api_token=<API_TOKEN>")
    with connect(f"{WS_BASE}/forex?api_token={api_token}") as ws:
        ws.send(json.dumps({"action": "subscribe", "symbols": symbol}))
        ws.recv()
        print("  Result:", json.loads(ws.recv()))


def websocket_crypto_quote(api_token: str, symbol: str) -> None:
    print(f"\nCrypto live quote\n  URL: {WS_BASE}/crypto?api_token=<API_TOKEN>")
    with connect(f"{WS_BASE}/crypto?api_token={api_token}") as ws:
        ws.send(json.dumps({"action": "subscribe", "symbols": symbol}))
        ws.recv()
        print("  Result:", json.loads(ws.recv()))


def main() -> None:
    api_token = load_app_config(CONFIG_PATH).eodhd.api_token
    day = date(2026, 7, 10)  # hardcoded trading day
    client = APIClient(api_token)

    exchange_trading_calendar(client, api_token, "US", day.isoformat())
    exchange_symbol_list(client, api_token, "US")
    bulk_daily_eod(client, api_token, "US", day.isoformat())
    bulk_dividends(client, api_token, "US", day.isoformat())
    bulk_splits(client, api_token, "US", day.isoformat())
    intraday_history(client, api_token, "AAPL.US", day)
    websocket_us_quote(api_token, "AAPL")
    websocket_forex_quote(api_token, "EURUSD")
    websocket_crypto_quote(api_token, "BTC-USD")


if __name__ == "__main__":
    main()
