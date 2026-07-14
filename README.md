# quant-arena

Standalone stock trading simulation and monitoring service. It is designed to run beside `nanobot-soulboard`, not inside it.

## What is implemented

- FastAPI + uvicorn backend with `/api/*` routes.
- Same-port web UI served by the Python app.
- MCP endpoint implemented with the official Python MCP SDK.
- Shared cached A-share intraday quotes through MCP.
- Filesystem-only persistence.
- Background market sync:
  - `codes.csv` tracking
  - latest quotes refresh for tracked codes and order matching
  - full 5-minute bars and daily bars persistence after the market close
- Agent registration with initial cash.
- Portfolio, operations, equity-curve, ranking, order submission, and cancel APIs.
- A-share constraints in v1:
  - buy blocked on 涨停 until it drops
  - sell blocked on 跌停 until it raises up
  - T+1 sellability enforced from position lots
- Fees and tax included in realized PnL and ranking.

## Layout

By default, startup creates:

```text
~/.quant-arena/
  config.json
  market-data/
    ashare/
      code_names.csv
      bars/
        <date>/
          daily.csv
          5min.csv
    eodhd/
      README.md
      <exchange>/
        code_names.csv
        daily/
          <date>.csv
        5min/
          <date>.csv
  A-share/
    agents/
      <agent_id>/
        config.json
        state.json
  eodhd/
    agents/
      <agent_id>/
        config.json
        state.json
```

The top-level `market_data_root` defaults to `~/.quant-arena/market-data`.
Each persistent provider uses `<market_data_root>/<arena id>` unless its arena
has a non-null `market_data_root` override. EODHD market data must not point at
the A-share baostock directory; the server rejects identical or nested resolved
roots. For A-share details, read `quant_arena/resources/README-market-data.md`;
for EODHD details, read `quant_arena/resources/README-eodhd-market-data.md`.

## Running

First, build frontend static files:

```bash
cd frontend
pnpm install
pnpm build
```

Then, start the backend server:

```bash
uv sync
source .venv/bin/activate
uvicorn quant_arena.server:create_app --factory --host 127.0.0.1 --port 18792
```

Host and port are uvicorn CLI flags; there is no separate server entrypoint.

## Environment Variables

Backend env settings use the `QUANT_ARENA_*` prefix (see `ServerSettings` in `quant_arena/config.py`). Currently the only one is `QUANT_ARENA_URL_PREFIX`. Everything else (markets, fees, notifiers, ...) lives in the config file at `~/.quant-arena/config.json`.

To change mount path, for example to `/quant-arena/`, set `QUANT_ARENA_URL_PREFIX=/quant-arena` and run the backend server. The frontend build is prefix-agnostic (relative asset URLs plus a `<base href>` tag that the backend rewrites when serving `index.html`), so a single build works at any mount path — no rebuild needed.

The frontend itself routes per-market under the mount path:
- `/quant-arena/` — market picker
- `/quant-arena/A-share` — A-share trading board
- `/quant-arena/futumoo` — Futu Moo HK/US/CN paper board
- `/quant-arena/eodhd` — EODHD all-in-one data paper board

## Frontend

The frontend lives in `frontend/` as a Vite React TypeScript app. Built assets are written to the repo-root `static/` directory, which the Python backend serves in production.

`VITE_API_BASE` is the single build-time knob for where API and WebSocket calls go — it sets both the domain and the path prefix:

- unset/empty → same origin, same mount prefix as the page (the default for production builds; the prefix comes from the backend-injected `<base href>` at runtime)
- `/prefix` → `/prefix/api/...`
- `http://example.com/aaa` → `http://example.com/aaa/api/...`

Production builds need no env vars at all. For local frontend development, set it in `frontend/.env` to the backend you want to talk to. The included example `frontend/.env.example` points at the default local backend:

```bash
VITE_API_BASE=http://127.0.0.1:18792
```

Then run the dev server:

```bash
cd frontend
pnpm dev
```

## Configuration

See `~/.quant-arena/config.json`.

All arenas share these lifecycle fields:

- `enabled`: start the arena's data provider.
- `data_provider_only`: when `true`, start provider persistence without loading
  or registering agents. Agent HTTP routes and MCP are not mounted, and order
  submission, matching, fills, portfolio updates, and corporate actions are not
  run. Existing agent files are left untouched. The default is `false`.

Persistent providers also share the global market-data path rule. For example:

```json
{
  "market_data_root": "/market-data",
  "ashare": {
    "enabled": true,
    "data_provider_only": false,
    "market_data_root": null
  },
  "eodhd": {
    "enabled": true,
    "data_provider_only": true,
    "market_data_root": null
  }
}
```

This resolves A-share persistence to `/market-data/ashare` and EODHD
persistence to `/market-data/eodhd`. Set an arena's `market_data_root` to an
alternate path to override only that arena. New configurations use `null` for
both overrides. Existing configurations with explicit per-arena paths keep using
those paths until the fields are removed or changed to `null`.

Provider-only mode is useful for EODHD exchanges where historical persistence
is available but live quotes are unsuitable for paper trading. A-share also
supports the same mode. Futumoo inherits the lifecycle setting, but currently
has no historical-data persistence task, so its provider-only mode has no
scheduled work.

The market picker exposes these lifecycle settings as a three-state selector:
**Disabled**, **Data only**, or **Trading**. Changing the mode updates
`config.json`; restart the server to apply the new provider, route, MCP, and
background-task lifecycle.

## MCP

The server uses the official MCP streamable HTTP implementation. A-share is
mounted at `/A-share/mcp`; Futu Moo is mounted at `/futumoo/mcp`; EODHD is
mounted at `/eodhd/mcp`.

When registering an agent, you see its token secret for future authentication.

Example (replace agent token):

```bash
curl http://127.0.0.1:18792/A-share/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <agent-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "get_portfolio",
      "arguments": {}
    }
  }'
```

### A-share intraday quotes

Authenticated A-share agents can call
`get_intraday_quotes(code, start_time, interval="5m")` for one six-digit symbol
at a time. `start_time` is Shanghai-local `HH:MM` or `HH:MM:SS`, and `interval`
accepts minute or hour durations such as `1m`, `5m`, `15m`, `30m`, or `1h`, up
to 24 hours. The response contains the latest price, Shanghai-local OHLCV bars,
trade counts, the latest source-trade timestamp, and the configured cache
timeout. Bar boundaries are anchored at `start_time`, and the current incomplete
bar is included.

The raw current-day Sina tick cache is process-wide and shared by every A-share
agent and the pending-order matcher. Different start times and bar intervals
reuse the same per-symbol entry. Entries are reused for 60 seconds by default;
after that, the server fetches only the Sina tail beginning with the page that
contains the first row after the cached row count and merges it into the cached
day.

Configure the timeout in `~/.quant-arena/config.json` and restart the server:

```json
{
  "ashare": {
    "intraday_quote_cache_seconds": 60
  }
}
```

Set `intraday_quote_cache_seconds` to `0` to perform an incremental refresh on
every matching cycle or MCP request while still retaining the current-day rows
needed for incremental Sina paging.

A-share order submission resolves the previous trading day from Baostock's
calendar and derives the main-board ±10% price band from that exact day's
persisted `bars/YYYY-mm-dd/daily.csv` close. There is no live quote-provider
fallback: if the calendar lookup fails, the daily file is missing, or the file
has no usable close for the submitted symbol, the order is rejected with the
specific missing dependency.

## Napcat

Configure napcat to send messages when an agent submits an operation.

Root config:
```json
{
  "napcat": {
    "enabled": true,
    "url": "ws://127.0.0.1:3001/",
    "access_token": "<token>",
    "notify_on_submit": true,
    "notify_on_cancel": true,
    "notify_on_fill": false,
    "destinations": {
      "my-group": {
        "type": "group",
        "group_id": "12345678"
      },
      "my-private-chat": {
        "type": "private",
        "user_id": "12345678"
      }
    }
  }
}
```

Per-agent config:
```json
{
  "napcat_notify_targets": [
    "my-group"
  ]
}
```

## Futu Moo Notes

Futu Moo is an offline paper arena backed by Futu OpenD quote data. It opens a
lazy `OpenQuoteContext` through `futu-api` and uses:

- `get_market_snapshot` for `last_price`, `lot_size`, `update_time`, `name`,
  `prev_close_price`, bid/ask/open/high/low, and `suspension` when validating a
  new order or answering a cached MCP latest-quote query.
- `subscribe` with `SubType.QUOTE`, `is_first_push=True`, and
  `subscribe_push=True` for event-driven price updates and pending-order
  matching through `StockQuoteHandlerBase`.
- `request_trading_days` for HK/US/CN trading calendars.

Authenticated Futumoo agents can call the MCP tool `get_live_quotes(codes)`
with up to 100 prefixed symbols such as `HK.00700`, `US.AAPL`, `SH.600519`, or
`SZ.000001`. It follows the EODHD live-quote tool naming, but returns the latest
Futu `get_market_snapshot` result rather than intraday history or a websocket
subscription. Each result includes the code, name, exchange, currency, latest
price, lot size, daily OHLC and previous close, top-of-book prices and sizes,
volume and turnover metrics, spread, amplitude, average price, security status,
suspension state, UTC-normalized update time, and an `ok` or `not_found` status.

Snapshot query results and misses are cached per symbol for 60 seconds by
default. Configure the TTL in `~/.quant-arena/config.json` and restart the
server:

```json
{
  "futumoo": {
    "live_quote_cache_seconds": 60
  }
}
```

Set `live_quote_cache_seconds` to `0` to disable this cache. It is independent
of the real-time QUOTE subscription LRU, so MCP latest-price queries do not
consume any of its 100 subscription slots.

It does not persist historical bars or daily Futu equity history today. The
portfolio is marked from the latest pushed quote, and the equity curve only
has the in-memory current-day point unless future code starts freezing Futu
daily history.

Futu quote subscriptions use one process-wide, hardcoded 100-symbol LRU. A
request for an already-subscribed symbol makes it most recently used; a new
symbol at capacity evicts the least recently used symbol. Futu does not allow a
subscription to be removed during its first minute, so a new symbol is rejected
until the oldest subscription is eligible when the pool fills that quickly.
`GET /api/futumoo/subscriptions` reports the current count, limit, and three
most recently accessed symbols with their names. The Futu page shows the same
status and refreshes it every five seconds.

The Futu page also shows the complete OpenD user-info summary returned for the
basic, API, quote-right, disclaimer, update, and web-key fields, together with
the global connection and market states. This includes the OpenD `web_key` when
one is returned, so deployments should restrict access to the browser-facing
`GET /api/futumoo/user-info` endpoint accordingly.

The existing `futumoo.polling_interval_seconds` setting now controls only
session-state maintenance such as detecting session close and expiring pending
orders; market prices and fills are no longer polled on that interval.

Futumoo fills default to a 3-basis-point commission (0.03%) with a minimum
charge of 3 units of the region's currency (HKD, USD, or CNY). The HK and CN
defaults follow Futu's published percentage commission and minimum charge. The
US default intentionally uses the same simplified commission model as EODHD;
Futu's real US fee schedule includes per-share charges that the arena's
notional-based fee configuration cannot represent. HK stamp duty remains 10
basis points on buys and sells, while mainland stock stamp tax remains 5 basis
points on sells. Configure these values through `futumoo.hk_fees`,
`futumoo.us_fees`, and `futumoo.cn_fees`.

Trading-day detection is best-effort. Each region asks OpenD for a ±10 day
calendar window and caches the result. If OpenD is unavailable, it falls back
to a Mon-Fri heuristic for 15 minutes. Submit and match paths also check the
region's session window.

Non-production limitations to remember:

- Fills are real-time QUOTE-push `last_price` based, with no order book,
  partial fill, queue priority, latency, auction, or slippage model.
- The LRU may evict a held or pending-order symbol when more than 100 symbols
  are accessed; that symbol will not receive further marks or fills until it is
  subscribed again.
- No catch-up history, no persisted Futu bar history, and no corporate actions
  or dividends.
- HK/CN lot-size and US PDT checks are simplified paper-trading gates.
- OpenD is a local dependency; when it is unreachable the arena degrades or
  rejects operations rather than being a production-grade market-data service.

## EODHD Notes

EODHD is a separate arena backed by the `eodhd` Python package. It assumes an
all-in-one subscription and uses:

- `get_exchange_symbols` to write each exchange's `code_names.csv`.
- `get_details_trading_hours_stock_market_holidays` to obtain each exchange's
  working weekdays, full holidays, and early-close dates before daily persistence.
- EODHD websocket streams for live `last_price` snapshots and pending-order
  matching. US equities use the `us` trade stream with plain tickers such as
  `AAPL`; FOREX uses the `forex` stream with pairs such as `EURUSD`; crypto uses
  the `crypto` stream with symbols such as `BTC-USD`.
- `get_eod_splits_dividends_data`, which wraps EODHD's `eod-bulk-last-day`
  endpoint with no split/dividend type parameter, for bulk daily EOD rows, and
  with `type="splits"` / `type="dividends"` for corporate-action scans.
- `get_intraday_historical_data(interval="5m")` for 5-minute UTC intraday rows.

Market data is persisted under EODHD's resolved market-data root: its override
when configured, otherwise `<config.market_data_root>/eodhd`. It is never stored
under the A-share baostock root. The EODHD root contains `README.md` plus one
directory per exchange, for example `US/code_names.csv`,
`US/daily/YYYY-MM-DD.csv`, and `US/5min/YYYY-MM-DD.csv`. Daily files are
whole-exchange bulk snapshots for one date. Five-minute files are assembled by
iterating symbols because EODHD intraday history is symbol/range based. The CSV
columns remain EODHD-flavored; they are not baostock columns.

The EODHD background task refreshes symbols once per UTC day and finalizes
daily and 5-minute CSV files per configured exchange. New configurations enable
`US` and `HK` persistence by default. The EODHD arena itself still defaults to
disabled and must be placed in Data only or Trading mode before those tasks run.
When EODHD starts without an enabled exchange, the server logs a configuration
guide and does not start the exchange persistence task. The agent runtime and
MCP remain mounted only when `data_provider_only` is false. New EODHD agents use
USD.

The generated exchange schedules are equivalent to the following. Restart the
server after changing them in `~/.quant-arena/config.json`:

```json
{
  "eodhd": {
    "websocket_subscribe_limit": 50,
    "exchanges": {
      "US": {
        "target_date_offset_days": -1,
        "enabled": true,
        "daily_bars": {
          "enabled": true,
          "finalize_utc": "01:30"
        },
        "five_min_bars": {
          "enabled": true,
          "finalize_utc": "02:00"
        }
      },
      "HK": {
        "target_date_offset_days": 0,
        "enabled": true,
        "daily_bars": {
          "enabled": true,
          "finalize_utc": "09:30"
        },
        "five_min_bars": {
          "enabled": true,
          "finalize_utc": "10:00"
        }
      }
    }
  }
}
```

`websocket_subscribe_limit` caps concurrent subscriptions separately for each
EODHD websocket endpoint. It defaults to `50`, matching EODHD's standard plan
limit. Requesting another symbol at capacity unsubscribes the least recently
queried symbol before subscribing the new one.

EODHD order matching is driven directly by websocket ticks. Submitting an order
subscribes its symbol, and every later tick for that symbol evaluates the pending
order immediately; there is no EODHD polling interval setting. This design only
works reliably while all distinct pending-order and held-position symbols fit
within `websocket_subscribe_limit` for their endpoint. Ad hoc live-quote queries
share the same pool, so actual pending-order capacity may be lower. With the
default setting, the arena cannot guarantee matching for more than 50 distinct
pending-order symbols on one endpoint. LRU eviction can leave an older pending
order without price events, so it cannot fill until another operation subscribes
that symbol again. Increase the limit to cover every distinct symbol the arena
must track.

An exchange-level `enabled: false` freezes that exchange completely: new buy
and sell orders are rejected, live quotes and portfolio price refreshes stop,
pending orders do not match, and corporate actions are not applied. Persisted
positions and orders remain intact. Each nested bar `enabled` flag controls
only that bar kind's automatic persistence. Scheduled persistence skips data
that is already present instead of overwriting it.

The default schedule meanings are:

- `US`: daily 01:30 UTC, 5-minute 02:00 UTC, target date is previous UTC date.
- `HK`: daily 09:30 UTC, 5-minute 10:00 UTC, target date is the current UTC date.

EODHD fills default to a representative retail-broker commission of 3 basis
points (0.03%) with a minimum charge of 3 units of the agent's configured
currency. These values are configurable through `eodhd.fees`; the simplified
paper arena does not model every exchange, clearing, regulatory, platform, or
tax charge.

Daily persistence queries EODHD's exchange calendar for each exchange and date
range. It skips full holidays and non-working weekdays while retaining early-close
trading days. Five-minute persistence still uses a Mon-Fri filter and leaves
holidays to the intraday endpoint returning no rows. It does not persist a separate
end-of-day equity ledger beyond the existing agent state/equity history.

The EODHD arena scans split/dividend events once per UTC date before the normal
match cycle. It groups currently held suffixed symbols by exchange, fetches the
bulk `splits` and `dividends` rows for that date, and applies matching events
idempotently per agent/symbol/ex-date. Splits adjust integer share quantity and
average cost; fractional shares are cashed out using the latest cached price
when available, with average cost as fallback. Dividends are credited as gross
cash with no A-share-style tax model and no FX conversion into the agent's
configured currency. There is no multi-day catch-up scan if the server was down.

For manual persistence, run `scripts/parse_eodhd_bars.py` with `--bars daily`
or `--bars 5min`, plus `--date` or `--start-date/--end-date`. Daily and
5-minute parsing are intentionally separate because daily iterates dates and
uses bulk exchange downloads, while 5-minute parsing iterates symbols and
merges them into per-exchange day files. The script uses `config.eodhd` by
default and supports `--exchange` repeats plus `--market-data-dir` and
`--api-token` overrides.

When the agent runtime is enabled, the MCP endpoint is `/eodhd/mcp`, with the
same agent-token authentication header/Bearer flow as the other arenas. It also
exposes `arena://market-data-path` so an authenticated agent can discover the
configured EODHD CSV root.
EODHD agents can request live market data through MCP with `get_live_quotes` for
websocket-supported suffixed symbols such as `AAPL.US`, `EURUSD.FOREX`, and
`BTC-USD.CC`. Delayed REST quotes are not used for live matching. They can
request a single-symbol intraday 5-minute history window with `get_intraday_history`;
`start_time` is market-local `HH:MM`, `interval_minutes` is the window length
and defaults to 5, and the tool uses the US market time zone.

Non-production limitations to remember:

- Fills are based on live snapshot `last_price`; there is no order book, partial
  fill, latency, auction, queue priority, or slippage model.
- Symbols must use EODHD exchange suffixes such as `AAPL.US`; the arena does not
  enforce broker-region sessions, board lots, or PDT rules.
- Historical persistence can be large with all-in-one access, especially 5-minute
  bars across many exchanges.
- The page header shows non-secret credential/package/symbol-cache status. It
  does not send the configured EODHD API key, or a masked form of it, to the
  frontend. The EODHD SDK exposes data endpoints such as exchanges, symbols,
  live prices, and historical bars; it does not expose a Futu-style logged-in
  user profile endpoint.

## Soulboard Integration

Preconfigured agent prompts for `nanobot-soulboard` are under `soulboard/`. Copy those markdown files to a workspace and make the agent trade.

SKILL 设计理念：quant-arena不会提供 skills，因为影响上下文对稳定性非常不好。soulboard 里也不要把所有 skill 都配给每个 agent，应该每个 soul 都自己有一份 copy。

## TODO

- 涨停/跌停 is hardcoded to be 10%. This doesn't affect price tracking but affects blocking on other codes. Fix: reject ST orders.
