import os
import finnhub
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ["FINNHUB_API_KEY"]
client = finnhub.Client(api_key=api_key)

today = date.today()
start = today - timedelta(days=7)

items = client.company_news(
    "DCTH",
    _from=start.isoformat(),
    to=today.isoformat(),
)

for x in items[:10]:
    print(datetime.fromtimestamp(x.get("datetime")), x.get("source"))
    print(x.get("headline"))
    print(x.get("summary"))
    print()
