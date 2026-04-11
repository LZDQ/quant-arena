# Market Data Usage Guide

Directory structure:

```
README.md          # this file
code_names.csv     # two columns: "code" and "name", for example 000001,平安银行
bars
└── YYYY-mm-dd
    ├── 5min.csv   # five minutes bars for all code that day
    └── daily.csv  # daily bars for all code that day
```

Example `5min.csv`:
```
date,time,code,open,high,low,close,volume,amount
2025-01-02,20250102093500000,000001,11.73,11.76,11.73,11.75,7409800,87039608.0
2025-01-02,20250102093500000,000002,7.25,7.31,7.25,7.28,8652800,63068888.0
...
2025-01-02,20250102150000000,688255,26.2000,26.2200,26.1800,26.2200,21638,567056.0000
2025-01-02,20250102150000000,688256,643.8800,645.6200,643.3000,645.6200,93387,60174336.0000
```

Example `daily.csv`:
```
date,code,open,high,low,close,preclose,volume,amount
2025-01-02,000001,11.73,11.77,11.39,11.43,11.7,181959699.0,2102923078.11
2025-01-02,000002,7.25,7.36,7.07,7.11,7.26,118266605.0,854487562.87
...
```

## Data Sources and Updates

The data is from baostock and updates every day after 9PM.

## Usage Guidelines

These data are read-only. You can use them for analysis and backtesting.

Do not make your own writable copy because it is a waste of disk space.

Do not go to baostock to fetch your own data unless they are not provided in this directory. Even if you do so, only fetch recent statistics and make no more than 1000 requests per day, focusing on only a small set of codes.

Within the limits, you can use whatever methods to analyze the data.
