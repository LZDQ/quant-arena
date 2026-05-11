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

```py
# Trading hours: 09:30–11:30, 13:00–15:00 Shanghai. Pre-open quotes from 09:25.
import akshare as ak

code = "600726"                          # 6-digit A-share code as a string
symbol = ak.stock_a_code_to_symbol(code) # "sh600726" or "sz000001" etc.

frame = ak.stock_intraday_sina(
    symbol=symbol,
    date="20260410",                     # YYYYMMDD; today's date for live data
)
# frame columns: ticktime, price, volume, prev_price, kind
# kind: '买盘' (buy), '卖盘' (sell), '中性盘' (neutral)
print(frame.tail())
```

适用场景：观察若干支股票的实时价格。

常见问题：

- 非交易日或 9:30 前：`stock_intraday_sina` 报错 `KeyError: 'ticktime'`。
- 停牌：frame 可能只包含一行，表示最后一次收盘价格。

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
