---
name: finnhub-news
description: 使用 finnhub 获取美股新闻
---

# Skill: Finnhub 获取美股新闻

## 依赖&环境

需要安装 `finnhub-python` 以及配置 `FINNHUB_API_KEY`。默认情况下，`FINNHUB_API_KEY` 会配置在当前的 `.env` 中，可以用 `python-dotenv` 的 `load_dotenv()` 加载（不需要读取 `.env` 的内容）。

## 代码片段

```py
import os
import finnhub
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ["FINNHUB_API_KEY"]
client = finnhub.Client(api_key=api_key)

today = date.today()
start = today - timedelta(days=7)

items = client.company_news(
    "NVDA",
    _from=start.isoformat(),
    to=today.isoformat(),
)

for x in items[:10]:
    print(datetime.fromtimestamp(x.get("datetime")), x.get("source"))
    print(x.get("headline"))
    print(x.get("summary"))
    print()
```

## 后期筛选

注意 finnhub 给出的结果中有一些垃圾信息，不看就行。不需要看原始文章，直接看 summary。
