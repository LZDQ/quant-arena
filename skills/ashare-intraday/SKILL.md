---
name: ashare-intraday
description: 使用 python akshare 获取 A股实时数据
---

# A股实时数据获取

## 使用场景

用于盯盘获取实时数据。

## 依赖

需要安装 `akshare`。

## 代码片段

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

## 常见报错

- **非交易日或 9:30 前：**`stock_intraday_sina` 报错 `KeyError: 'ticktime'`。
- **停牌：**frame 可能只包含一行，表示最后一次收盘价格。
