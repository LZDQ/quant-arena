---
name: ashare-intraday
description: Fetch live intraday tick data for A-share stocks using akshare's stock_intraday_sina.
allowed-tools: Bash, Read
---

# A-Share Intraday Quote Fetching

## When to use

Monitoring a small list of codes during market hours.

## Code recipe

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

## Failure modes to handle

- **Non-trading day:** `stock_intraday_sina` raises `KeyError: 'ticktime'`.
- **Suspended stocks:** frame may contain only one row at the prior close. Inspect `volume` to detect.
