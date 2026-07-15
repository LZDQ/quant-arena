from datetime import date

import akshare as ak
import pandas as pd
import requests

if False:
    code = '600726'
    symbol = ak.stock_a_code_to_symbol(code)
    print(symbol)  # sh600726

    frame = ak.stock_intraday_sina(
        symbol=symbol,
        date='20260420',
    )
    print(frame)


def stock_intraday_sina_custom(code: str) -> pd.DataFrame:
    symbol = ak.stock_a_code_to_symbol(code)
    count_url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_Bill.GetBillListCount"
    )
    params = {
        "symbol": symbol,
        "page": "1",
        "sort": "ticktime",
        "asc": "1",
        "day": date.today().isoformat(),
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
    total_count = int(requests.get(count_url, params=params, headers=headers).json())
    assert total_count > 0

    list_url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_Bill.GetBillList"
    )
    params["num"] = str(total_count)
    frame = pd.DataFrame(
        requests.get(list_url, params=params, headers=headers).json()
    )
    assert not frame.empty
    frame.sort_values(by=["ticktime"], inplace=True, ignore_index=True)
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame["prev_price"] = pd.to_numeric(frame["prev_price"], errors="coerce")
    frame["code"] = code
    return frame


frame = stock_intraday_sina_custom("600726")
print(frame.head())
print(frame.tail())
