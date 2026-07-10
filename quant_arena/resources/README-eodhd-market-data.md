# EODHD Market Data Usage Guide

This directory contains EODHD-flavored CSV data. It intentionally does not
share the A-share baostock directory because the schemas and vendor semantics
are different.

## Directory Structure

```text
README.md
<exchange>/
├── code_names.csv
├── daily/
│   └── YYYY-MM-DD.csv
└── 5min/
    └── YYYY-MM-DD.csv
```

For example, Hong Kong data lives under `HK/`, US data lives under `US/`,
Shanghai data lives under `SHG/`, and Shenzhen data lives under `SHE/`.

## Exchange Codes

EODHD's `US` exchange code is a unified US market. It already includes major
US venues such as NASDAQ and NYSE, plus NYSE ARCA and OTC markets. Use `US/`
for broad US coverage. Use separate exchange codes such as `NASDAQ/` or
`NYSE/` only when you intentionally want narrower, venue-specific files; do
not configure `US`, `NASDAQ`, and `NYSE` together unless you want overlapping
data in separate directories.

## <exchange>/code_names.csv

The symbol table is written from EODHD exchange-symbol-list responses.
Columns are EODHD-oriented:

```text
symbol,code,exchange,name,type,currency,isin,country
```

`symbol` is the fully-qualified EODHD symbol such as `AAPL.US`.

## <exchange>/daily/YYYY-MM-DD.csv

Daily rows come from EODHD bulk EOD data for one exchange and one date. The
parser downloads these by date because EODHD exposes daily bars as a bulk
exchange snapshot. Columns:

```text
date,symbol,code,exchange,open,high,low,close,adjusted_close,volume
```

## <exchange>/5min/YYYY-MM-DD.csv

Five-minute rows come from EODHD intraday data. EODHD intraday timestamps are
UTC Unix timestamps. The parser assembles these files by iterating symbols
because EODHD exposes intraday history as a single-symbol date-range endpoint.
Columns:

```text
date,datetime_utc,timestamp,gmtoffset,symbol,code,exchange,open,high,low,close,volume
```

## Corporate Actions

Split and dividend events are not stored as CSV files in this market-data
tree. The EODHD arena scans them directly from the bulk endpoint using
`type="splits"` and `type="dividends"` once per UTC date, grouped by exchanges
that have currently held positions.

Applied split/dividend records are persisted in each agent's EODHD state.
Splits adjust integer share quantity and average cost, with fractional shares
cashed out. Dividends are credited as gross cash. The EODHD arena does not apply
the A-share dividend tax model and does not perform FX conversion.

## Notes

This layout is intentionally separated by EODHD exchange. Root-level
`code_names.csv` and root-level `bars/` are legacy shapes and are not read or
written by the current EODHD persistence layer. The CSV columns are not
baostock columns; use the schemas above rather than assuming A-share field
names.
