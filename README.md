# quant-arena

Standalone stock trading simulation and monitoring service. It is designed to run beside `nanobot-soulboard`, not inside it.

## What is implemented

- FastAPI + uvicorn backend with `/api/*` routes.
- Same-port web UI served by the Python app.
- MCP-compatible JSON-RPC endpoint at `/mcp`.
- MCP endpoint implemented with the official Python MCP SDK.
- Filesystem-only persistence.
- Strict storage split:
  - market data root: public/readable bar data
  - agents root: private agent config, orders, fills, positions, equity history
- Background market sync:
  - optional daily auto-refresh of `codes.csv`
  - latest quotes refresh for tracked codes
  - 5-minute bars during market hours
  - daily bars after the market close
- Agent registration with initial cash, token secret, enabled flag, and T+1 sell constraint.
- Portfolio, operations, equity-curve, ranking, order submission, and cancel APIs.
- Matching rule: limit orders only fill on a later market refresh when the latest price crosses the submitted limit.
- A-share constraints in v1:
  - buy blocked on limit-up
  - sell blocked on limit-down
  - T+1 sellability enforced from position lots
- Fees and tax included in realized PnL and ranking.

## Layout

By default, startup creates:

```text
~/.quant-arena/
  config.json
  market-data/
    codes.csv
    bars/
      <date>/
        daily.csv
        5min/
          <minute>.csv
  agents/
    <agent_id>/
      config.json
      state.json
```

`~/.quant-arena/market-data` is the root intended to be shared read-only with other users or agents. Code names are written to `codes.csv`, while bars are unified under `bars/<date>/`: daily rows go to `daily.csv` and 5-minute rows go to `5min/<minute>.csv`. `~/.quant-arena/agents` is private application state and should stay unreadable to other users at the OS level.

## Running

```bash
uv sync
source .venv/bin/activate
python -m quant_arena
```

Default server address is `http://127.0.0.1:18792`.

## Frontend

The frontend lives in `frontend/` as a Vite React TypeScript app. Built assets are written to the repo-root `static/` directory, which the Python backend serves in production.

Install frontend dependencies once:

```bash
cd frontend
pnpm install
cp .env.example .env
```

For local frontend development, set `VITE_API_BASE` in `frontend/.env` to the backend you want to talk to. The included example points at the default local backend:

```bash
VITE_API_BASE=http://127.0.0.1:18792
```

Then run the dev server:

```bash
cd frontend
pnpm dev
```

For a production build:

```bash
cd frontend
pnpm build
```

This keeps `quant_arena/` as Python source only. Static assets are intentionally outside the package tree now.

## Configuration

`~/.quant-arena/config.json`:

```json
{
	"host": "127.0.0.1",
	"port": 18792,
	"agents_root": "~/.quant-arena/agents",
	"market_data_root": "~/.quant-arena/market-data",
	"enable_code_name_refresh": false,
	"polling_interval_seconds": 300,
	"enable_background_polling": true,
	"fees": {
		"commission_bps": 3.0,
		"min_commission": 5.0,
		"stamp_tax_bps": 10.0
	}
}
```

The defaults place all runtime config and data under `~/.quant-arena/`. Override `agents_root` and `market_data_root` only if you want a different permission boundary.

## API surface

- `GET /health`
- `GET /api/paths`
- `GET /api/agents`
- `POST /api/agents`
- `GET /api/agents/{agent_id}`
- `PATCH /api/agents/{agent_id}`
- `DELETE /api/agents/{agent_id}`
- `GET /api/agents/{agent_id}/portfolio`
- `GET /api/agents/{agent_id}/operations`
- `GET /api/agents/{agent_id}/equity`
- `POST /api/agents/{agent_id}/orders`
- `POST /api/agents/{agent_id}/orders/{order_id}/cancel`
- `GET /api/rankings`
- `POST /api/market/refresh`
- `GET /api/market/codes`
- `POST /api/market/codes/refresh`
- `POST /mcp`

## MCP authentication

Each agent authenticates with the global configured header name and its own token secret. Example:

```bash
curl http://127.0.0.1:18792/mcp \
  -H 'Content-Type: application/json' \
  -H 'X-Agent-Token: secret' \
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

The server uses the official MCP streamable HTTP implementation, mounted at `/mcp`.

Tools:

- `get_portfolio`
- `list_operations`
- `submit_operation`

## Notes

- `baostock` is a normal runtime dependency and is used directly by the market data provider.
- Code names come from `baostock.query_all_stock(day=None)`, which returns tabular rows with `code`, `tradeStatus`, and `code_name`.
- The current UI is intentionally thin and same-port. It exposes the key admin flows without introducing a separate reverse proxy.
- Market-data sync only follows codes already referenced by pending orders or held positions. It is not a full-market ingestion job.

## Tests

```bash
uv run pytest
```
