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

prices = ak.stock_zh_a_spot()
print(prices)

"""
            代码    名称     最新价   涨跌额    涨跌幅      买入  ...      今开      最高      最低        成交量          成交额       时间戳
0     bj920000  安徽凤凰   15.98  0.25  1.589   15.98  ...   15.75   16.08   15.75    86780.0    1386846.0  10:02:14
...
5506  sz301682  宏明电子  132.66  7.03  5.596  132.65  ...  126.51  133.86  126.51  2217436.0  290404271.0  10:02:33
"""
```

注意该接口返回的代码带前缀。

高峰期可能会有 `RemoteDisconnect` 报错，可以加入重试逻辑。千万不要用 eastmoney 的接口，因为它非常不稳定。用例子中的这个 `stock_zh_a_spot`。
