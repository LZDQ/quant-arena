---
name: basic-momentum-analysis
description: 从 T-1 日线 + T-1 五分钟数据筛选次日主板强势候选股
---

# Skill: 主板动量候选股筛选

从 T-1 日线 + T-1 五分钟数据中，静态筛选次日可跟踪的主板强势候选股。

---

## 背景

算法本身很朴素：它不会预测涨停，也没有因子组合、回测或风险模型。它做的事情是：从所有主板非 ST 股票中，把"昨天涨幅居中偏强、流动性足够、且全天没有走坏"的票筛出来，再配合外部信息源（公告、新闻、板块逻辑）人工判断。

---

## 算法

```py
import csv

# ── 配置 ────────────────────────────────────────────────────────────────────
MARKET_DATA_ROOT = '/market-data'
DATE = '2026-04-14'   # T-1 日期，改成实际前一交易日

PCT_LO      = 6.0    # 涨跌幅下限 %
PCT_HI      = 9.95   # 涨跌幅上限（排除一字板）
AMOUNT_MIN  = 8e8    # 流动性底线（元）
TOP_N_DAILY = 60     # 进入盘中评分的候选数量
TOP_N_OUT   = 20     # 最终输出条数

# 主板前缀白名单 / 黑名单
PREFIX_OK  = ('600','601','603','605','000','001','002')
PREFIX_BAD = ('300','688')
# ────────────────────────────────────────────────────────────────────────────

names = {}
with open(f'{MARKET_DATA_ROOT}/code_names.csv', newline='', encoding='utf-8') as f:
    for r in csv.DictReader(f):
        c, n = r['code'], r['name']
        if (c.startswith(PREFIX_OK)
                and not c.startswith(PREFIX_BAD)
                and 'ST' not in n.upper()
                and '退' not in n):
            names[c] = n

# Step 1+2: 日线过滤
rows = []
with open(f'{MARKET_DATA_ROOT}/bars/{DATE}/daily.csv', newline='') as f:
    for r in csv.DictReader(f):
        c = r['code']
        if c not in names:
            continue
        try:
            pre    = float(r['preclose'])
            close  = float(r['close'])
            amount = float(r['amount'])
            high   = float(r['high'])
            low    = float(r['low'])
        except (ValueError, KeyError):
            continue
        if pre <= 0:
            continue
        pct = (close / pre - 1) * 100
        if PCT_LO <= pct < PCT_HI and amount >= AMOUNT_MIN:
            rows.append((c, names[c], pct, amount, close, pre, high, low))

rows.sort(key=lambda x: (x[2], x[3]), reverse=True)
candidates = [x[0] for x in rows[:TOP_N_DAILY]]

# Step 3: 盘中三档强度（T-1 5min）
stat = {c: {'1000': None, '1030': None, '1100': None} for c in candidates}
with open(f'{MARKET_DATA_ROOT}/bars/{DATE}/5min.csv', newline='') as f:
    for r in csv.DictReader(f):
        c = r['code']
        if c not in stat:
            continue
        try:
            cl = float(r['close'])
        except (ValueError, KeyError):
            continue
        t = r['time']
        if t.endswith('100000000'): stat[c]['1000'] = cl
        if t.endswith('103000000'): stat[c]['1030'] = cl
        if t.endswith('110000000'): stat[c]['1100'] = cl

# Step 4: 评分 + 输出
scored = []
for c, n, pct, amt, close, pre, high, low in rows:
    s = stat[c]
    if not s['1000'] or not s['1030'] or not s['1100']:
        continue
    a = (s['1000'] / pre - 1) * 100   # 10:00 涨跌幅
    b = (s['1030'] / pre - 1) * 100   # 10:30 涨跌幅
    d = (s['1100'] / pre - 1) * 100   # 11:00 涨跌幅
    # 早强晚弱惩罚：a 比 d 高越多扣越多
    score = 0.35*a + 0.35*b + 0.3*d + 0.15*pct - max(0, a - d) * 0.5
    scored.append((score, c, n, pct, amt, a, b, d))

scored.sort(reverse=True)
for score, c, n, pct, amt, a, b, d in scored[:TOP_N_OUT]:
    print('%s %s score=%.2f dchg=%.2f amt=%.1f亿 10:00=%.2f 10:30=%.2f 11:00=%.2f'
          % (c, n, score, pct, amt/1e8, a, b, d))
```

## 参数调整建议

| 参数 | 当前值 | 说明 |
|---|---|---|
| `PCT_LO` | 6.0 | 下限可调低至 5.0 以扩大候选池 |
| `PCT_HI` | 9.95 | 保留，避开一字板 |
| `AMOUNT_MIN` | 8e8 | 流动性底线，小票可调低至 3e8 |
| `TOP_N_DAILY` | 60 | 进入盘中评分的候选数量 |
| 评分权重 | 0.35/0.35/0.3 | 三档时间的权重，可根据市场偏好调整 |

## 投资建议

该算法只用到了最近的数据，完全不考虑股票具体业务、历史行情，只能充当初步动量分析，不能作为实际结果。务必使用其他外部信源，或自己修改算法用上更久之前的数据。
