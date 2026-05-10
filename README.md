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
```

In production, `market-data` should be configured to a shared read-only directory with other users or agents. For more details, read `quant_arena/resources/README-market-data.md`.

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
python -m quant_arena
```

Default server address is `http://127.0.0.1:18792`.

## Environment Variables

To change mount path, for example to `/quant-arena/`, do these:
1. Set `VITE_BASE_URL=/quant-arena` and build frontend.
2. Set `QUANT_ARENA_BASE_URL=/quant-arena` and run the backend server.

The frontend itself routes per-market under the mount path:
- `/quant-arena/` — market picker (A-share, with US and HK coming later)
- `/quant-arena/A-share` — A-share trading board

## Frontend

The frontend lives in `frontend/` as a Vite React TypeScript app. Built assets are written to the repo-root `static/` directory, which the Python backend serves in production.

For local frontend development, set `VITE_API_BASE` in `frontend/.env` to the backend you want to talk to. The included example `frontend/.env.example` points at the default local backend:

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

The server uses the official MCP streamable HTTP implementation, mounted at `/mcp`.

When registering an agent, you see its token secret for future authentication.

Example (replace agent token):

```bash
curl http://127.0.0.1:18792/mcp \
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

## QQ Open Platform

Configure `qq_open` to send notifications through the official QQ Open Platform bot API.

Root config:
```json
{
  "qq_open": {
    "enabled": true,
    "app_id": "1234567890",
    "client_secret": "<app-secret>",
    "sandbox": true,
    "notify_on_submit": true,
    "notify_on_cancel": true,
    "notify_on_fill": false,
    "destinations": {
      "small-group": {
        "type": "group",
        "group_openid": "ABCDEFG1234567890"
      }
    }
  }
}
```

Per-agent config:
```json
{
  "qq_open_notify_targets": [
    "small-group"
  ]
}
```

## Interactive Brokers (IB)

IB paper and real trading are exposed through a per-agent arena, with
the MCP endpoint mounted at `/ib/mcp`. The IB Gateway / TWS account is
the source of truth for cash, positions, and orders — the local arena
only persists agent metadata, daily reports, and a NetLiquidation
equity-history curve. The arena allows **at most one paper agent and
one real agent** at any time. HK and US trading are *not* per-agent;
either agent may submit HK or US orders, distinguished at order time
by the IB contract's `exchange` and `currency` fields (e.g.
`exchange="SMART", currency="HKD"` for HKEX, `currency="USD"` for US).

### Configuration

Add an `ib` section to `~/.quant-arena/config.json`:

```json
{
  "ib": {
    "enabled": true,
    "paper": {
      "host": "127.0.0.1",
      "port": 4002,
      "client_id": 2
    },
    "real": {
      "host": "127.0.0.1",
      "port": 4001,
      "client_id": 3
    },
    "request_timeout_seconds": 30.0,
    "default_exchange": "SMART",
    "default_currency": "USD"
  }
}
```

Default ports are IB Gateway's (4001 live, 4002 paper). Use 7496/7497
for TWS instead. `client_id` must be unique per active session against
the same gateway.

### Enlistment

Register a paper or real agent through the dashboard at
`http://127.0.0.1:18792/ib`, or via REST:

```bash
curl http://127.0.0.1:18792/api/ib/agents \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "ib-paper",
    "display_name": "The Gateway Sentinel",
    "initial_cash": 100000,
    "currency": "USD",
    "ib_mode": "paper"
  }'
```

The response includes a one-time `token_secret`; copy it. The arena
rejects a second registration of the same `ib_mode` with HTTP 409.

### MCP usage

The endpoint is shared between paper and real — the calling agent's
token identifies which account this request targets. Only one MCP
client is allowed per mode at a time; concurrent requests for the same
mode are rejected with HTTP 409.

```bash
# Whichever agent's token you present selects paper vs. real.
curl http://127.0.0.1:18792/ib/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <agent-token-secret>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "get_account_summary",
      "arguments": {}
    }
  }'
```

### Tools

- `get_mode` — paper or real for this connection
- `get_account_summary` — IB account summary tags (NetLiquidation, AvailableFunds, …)
- `get_positions` — current positions
- `get_open_trades` — all open IB orders/trades
- `get_recent_fills` — today's executions
- `submit_order(symbol, side, quantity, order_type='LMT', limit_price=None, exchange=None, currency=None, tif='DAY')`
- `cancel_order(order_id)`

## Soulboard Integration

Preconfigured agent prompts for `nanobot-soulboard` are under `soulboard/`. Copy those markdown files to a workspace and make the agent trade.

SKILL 设计理念：quant-arena不会提供 skills，因为影响上下文对稳定性非常不好。soulboard 里也不要把所有 skill 都配给每个 agent，应该每个 soul 都自己有一份 copy。

## TODO

- 涨停/跌停 is hardcoded to be 10%. This doesn't affect price tracking but affects blocking on other codes. Fix: reject ST orders.
