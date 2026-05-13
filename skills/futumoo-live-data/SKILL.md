---
name: futumoo-live-data
description: 使用 python 获取所有市场实时数据
---

# 富途牛牛全球实时数据获取

## 使用场景

用于盯盘获取实时数据。

## 依赖

需要在虚拟环境中安装 `futu-api`。

## 代码片段

检查是否正常运行：

```py
from futu import OpenQuoteContext

HOST = "127.0.0.1"

q = OpenQuoteContext(host=HOST, port=11111)
print(q.get_user_info())
q.close()
```

港股：

```py
from futu import OpenQuoteContext

HOST = "127.0.0.1"
CODE = "HK.00700"  # 腾讯控股

q = OpenQuoteContext(host=HOST, port=11111)
ret, data = q.get_market_snapshot([CODE])
if ret == 0:
    print(data[["code", "last_price"]])
q.close()
```

美股：

```py
from futu import OpenQuoteContext

HOST = "127.0.0.1"
CODE = "US.AAPL"  # Apple

q = OpenQuoteContext(host=HOST, port=11111)
ret, data = q.get_market_snapshot([CODE])
if ret == 0:
    print(data[["code", "last_price"]])
q.close()
```
