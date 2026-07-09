# quant-arena

Standalone stock trading simulation and monitoring service. It is designed to run beside `nanobot-soulboard`, not inside it.

## What is implemented

- FastAPI + uvicorn backend with `/api/*` routes.
- Same-port web UI served by the Python app.
- MCP endpoint implemented with the official Python MCP SDK.
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
  A-share/
    market-data/
      code_names.csv
      bars/
        <date>/
          daily.csv
          5min.csv
    agents/
      <agent_id>/
        config.json
        state.json
  eodhd/
    market-data/
      README.md
      code_names.csv
      bars/
        <date>/
          daily.csv
          5min.csv
    agents/
      <agent_id>/
        config.json
        state.json
```

In production, each arena's `market_data_root` can be configured in
`~/.quant-arena/config.json`. EODHD market data must not point at the A-share
baostock directory; the server rejects identical or nested roots. For A-share
details, read `quant_arena/resources/README-market-data.md`; for EODHD details,
read `quant_arena/resources/README-eodhd-market-data.md`.

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
  `prev_close_price`, bid/ask/open/high/low, and `suspension`.
- `request_trading_days` for HK/US/CN trading calendars.

It does not persist historical bars or daily Futu equity history today. The
portfolio is marked from the latest snapshot cache, and the equity curve only
has the in-memory current-day point unless future code starts freezing Futu
daily history.

Trading-day detection is best-effort. Each region asks OpenD for a ±10 day
calendar window and caches the result. If OpenD is unavailable, it falls back
to a Mon-Fri heuristic for 15 minutes. Submit and match paths also check the
region's session window.

Non-production limitations to remember:

- Fills are snapshot `last_price` based, with no order book, partial fill,
  queue priority, latency, auction, or slippage model.
- No catch-up history, no persisted Futu bar history, and no corporate actions
  or dividends.
- HK/CN lot-size and US PDT checks are simplified paper-trading gates.
- OpenD is a local dependency; when it is unreachable the arena degrades or
  rejects operations rather than being a production-grade market-data service.

## EODHD Notes

EODHD is a separate arena backed by the `eodhd` Python package. It assumes an
all-in-one subscription and uses:

- `get_exchange_symbols` to write `code_names.csv`.
- `get_live_stock_prices` for live `last_price` snapshots and pending-order
  matching. Bulk symbols are requested through the SDK's `s=` parameter.
- `get_eod_splits_dividends_data` for bulk daily EOD rows.
- `get_intraday_historical_data(interval="5m")` for 5-minute UTC intraday rows.

Market data is persisted under `config.eodhd.market_data_root`, never under the
A-share baostock root. The directory shape mirrors A-share for compatibility:
`README.md`, `code_names.csv`, and `bars/YYYY-MM-DD/{daily.csv,5min.csv}`. The
CSV columns remain EODHD-flavored; they are not baostock columns.

The EODHD background task refreshes symbols once per UTC day and finalizes
yesterday's daily and 5-minute CSV files after the configured UTC times. It uses
a Mon-Fri business-day filter before making historical requests; holidays are
left to the EODHD API returning empty/no rows. It does not persist a separate
end-of-day equity ledger beyond the existing agent state/equity history.

For manual bulk persistence, run `scripts/parse_eodhd_bars.py` with `--date` or
`--start-date/--end-date`. It uses `config.eodhd` by default and supports
`--exchange` repeats plus `--market-data-dir` and `--api-token` overrides.

The MCP endpoint is `/eodhd/mcp`, with the same agent-token authentication
header/Bearer flow as the other arenas. It also exposes
`arena://market-data-path` so an authenticated agent can discover the configured
EODHD CSV root.

Non-production limitations to remember:

- Fills are based on live snapshot `last_price`; there is no order book, partial
  fill, latency, auction, queue priority, or slippage model.
- Symbols must use EODHD exchange suffixes such as `AAPL.US`; the arena does not
  enforce broker-region sessions, board lots, or PDT rules.
- Historical persistence can be large with all-in-one access, especially 5-minute
  bars across many exchanges.
- The page header shows configured token/package/cache status. The EODHD SDK
  does not expose a Futu-style logged-in user profile endpoint.

## Soulboard Integration

Preconfigured agent prompts for `nanobot-soulboard` are under `soulboard/`. Copy those markdown files to a workspace and make the agent trade.

SKILL 设计理念：quant-arena不会提供 skills，因为影响上下文对稳定性非常不好。soulboard 里也不要把所有 skill 都配给每个 agent，应该每个 soul 都自己有一份 copy。

## TODO

- 涨停/跌停 is hardcoded to be 10%. This doesn't affect price tracking but affects blocking on other codes. Fix: reject ST orders.
