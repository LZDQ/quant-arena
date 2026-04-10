import logging
from datetime import date
from pathlib import Path
import time

from quant_arena.market import MarketService

logging.basicConfig(level=logging.INFO)

market = MarketService(market_data_root=Path("/Users/ldq/.quant-arena/market-data"))
market.refresh_code_names()
print(market.get_code_names())
while True:
    print(market.refresh_intraday(
        {"600726"},
        today=date(2026, 4, 10),
    ))
    time.sleep(10)
