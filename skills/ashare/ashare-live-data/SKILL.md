---
name: ashare-live-data
description: 通过 quant-arena MCP 获取 A股实时数据
---

# A股实时数据获取

## 使用场景

用于盯盘获取实时数据。

## 数据入口

实时行情统一通过 quant-arena MCP 的 `get_intraday_quotes` 工具获取。服务端会在所有
agent 和撮合程序之间共享缓存，并在缓存过期后增量请求新浪。不要直接请求新浪，也不要
调用 `ak.stock_intraday_sina`，否则会绕过共享缓存并增加新浪限流风险。

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

每次调用只传一支六位股票代码：

- `code`: 例如 `600726` 或 `000001`。
- `start_time`: 上海时间，格式为 `HH:MM` 或 `HH:MM:SS`。
- `interval`: K 线周期，例如 `1m`、`5m`、`15m`、`30m` 或 `1h`。

例如，调用 `get_intraday_quotes(code="600726", start_time="09:30", interval="5m")`。
返回结果中的 `latest_price` 是最新成交价，`bars` 包含 OHLCV、成交笔数以及每根 K 线
的上海本地起止时间，`as_of` 表示当前缓存里最新一笔新浪成交的时间。不同周期会复用
相同的服务端原始成交缓存。
