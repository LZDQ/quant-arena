# EODHD Market Data Usage Guide

This directory contains EODHD-flavored CSV data. It intentionally does not
share the A-share baostock directory because the schemas and vendor semantics
are different.

## Directory Structure

```text
README.md
code_names.csv
bars/
└── YYYY-MM-DD/
    ├── daily.csv
    └── 5min.csv
```

## code_names.csv

The symbol table is written from EODHD exchange-symbol-list responses.
Columns are EODHD-oriented:

```text
symbol,code,exchange,name,type,currency,isin,country
```

`symbol` is the fully-qualified EODHD symbol such as `AAPL.US`.

## daily.csv

Daily rows come from EODHD bulk EOD data. Columns:

```text
date,symbol,code,exchange,open,high,low,close,adjusted_close,volume
```

## 5min.csv

Five-minute rows come from EODHD intraday data. EODHD intraday timestamps are
UTC Unix timestamps. Columns:

```text
date,datetime_utc,timestamp,gmtoffset,symbol,code,exchange,open,high,low,close,volume
```

## Notes

This layout mirrors the baostock directory shape for compatibility, but the
CSV columns are not baostock columns. Use the schemas above rather than
assuming A-share field names.
