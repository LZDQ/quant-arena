import logging
from datetime import date
from pathlib import Path
import time

from quant_arena.market import MarketService

logging.basicConfig(level=logging.INFO)

market = MarketService(market_data_root=Path("/Users/ldq/.quant-arena/market-data"))
# market.refresh_code_names()
print(market.get_code_names())
# market.finalize_market_data_after_market_closed(today=date(2026, 4, 9), update_every=10)
while True:
    print(market.sync_live_five_minute_bars({'sh.600726'}, today=date(2026,4,10)))
    time.sleep(100)
