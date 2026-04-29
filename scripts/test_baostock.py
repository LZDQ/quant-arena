import baostock as bs
from datetime import date, datetime

bs.login()
result = bs.query_trade_dates(
    date(2026,4,21),  # works with date objects
    date(2026,4,21),
)
print(result.get_data())
