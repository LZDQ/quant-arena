from datetime import date, timedelta

from futu import OpenQuoteContext, SubType


HOST = "127.0.0.1"
PORT = 11111
SNAPSHOT_CODES = ["HK.00700", "US.AAPL", "SH.600519", "SZ.000001"]
ORDER_BOOK_CODE = "HK.00700"


def get_market_snapshot(quote_ctx: OpenQuoteContext) -> None:
    print("get_market_snapshot")
    print(quote_ctx.get_market_snapshot(SNAPSHOT_CODES))


def request_trading_days(quote_ctx: OpenQuoteContext) -> None:
    print("request_trading_days")
    today = date.today()
    start = (today - timedelta(days=10)).isoformat()
    end = (today + timedelta(days=10)).isoformat()
    for market in ("HK", "US", "CN"):
        print(market, quote_ctx.request_trading_days(market=market, start=start, end=end))


def get_user_info(quote_ctx: OpenQuoteContext) -> None:
    print("get_user_info")
    print(quote_ctx.get_user_info(info_field=[1, 2, 4, 8, 16]))


def get_global_state(quote_ctx: OpenQuoteContext) -> None:
    print("get_global_state")
    print(quote_ctx.get_global_state())


def subscribe_order_book(quote_ctx: OpenQuoteContext) -> None:
    print("subscribe")
    print(
        quote_ctx.subscribe(
            [ORDER_BOOK_CODE],
            [SubType.ORDER_BOOK],
            subscribe_push=False,
        )
    )


def get_order_book(quote_ctx: OpenQuoteContext) -> None:
    print("get_order_book")
    print(quote_ctx.get_order_book(ORDER_BOOK_CODE))


def main() -> None:
    quote_ctx = OpenQuoteContext(host=HOST, port=PORT)
    try:
        get_market_snapshot(quote_ctx)
        request_trading_days(quote_ctx)
        get_user_info(quote_ctx)
        get_global_state(quote_ctx)
        subscribe_order_book(quote_ctx)
        get_order_book(quote_ctx)
    finally:
        quote_ctx.close()


if __name__ == "__main__":
    main()
