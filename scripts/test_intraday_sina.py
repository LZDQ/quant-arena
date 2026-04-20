import akshare as ak

code = '600726'
symbol = ak.stock_a_code_to_symbol(code)
print(symbol)  # sh600726

frame = ak.stock_intraday_sina(
    symbol=symbol,
    date='20260420',
)
print(frame)
