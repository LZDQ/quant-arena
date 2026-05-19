---
name: ashare-live-data
description: 使用 python 获取 A股实时数据
---

# A股实时数据获取

## 使用场景

用于盯盘获取实时数据。

## 依赖

需要安装 `akshare` 和 `baostock`。

## 代码片段

### 检查是不是交易日

使用以下代码检查今天是否是交易日：

```py
from datetime import date
import baostock as bs

today = date.today()
bs.login()
print(bs.query_trade_dates(today, today).get_data())
bs.logout()
```

### 获取单支股票实时数据

标准接口为 `ak.stock_intraday_sina`，但**不要直接使用它**，因为它会调用多次新浪后端 API 造成访问受限。

使用以下修改后的代码片段：

```py
from datetime import date

import akshare as ak
import pandas as pd
import requests


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
print(frame.tail())
```

### 获取所有股票实时数据

适用场景：盘中重新跑量化算法。数据需要结合之前的日线，配合今天的实时价格跑全量算法。

```py
import akshare as ak

prices = None
for retry in range(3):
    try:
        prices = ak.stock_zh_a_spot()
        break
    except:
        pass

if prices is None:
    print('Failed to fetch all data')
    exit(1)

code = "600726"                          # 6-digit A-share code as a string
symbol = ak.stock_a_code_to_symbol(code) # "sh600726" or "sz000001" etc.
print(prices[prices['代码'] == symbol])
```

高峰期可能会有 `RemoteDisconnect` 报错，可以加入重试逻辑。千万不要用 eastmoney 的接口，因为它非常不稳定。用例子中的这个 `stock_zh_a_spot`。
