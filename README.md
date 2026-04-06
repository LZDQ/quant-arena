# quant-arena

Standalone stock trading simulation and monitoring service. It is designed to run beside `nanobot-soulboard`, not inside it.

## What is implemented

- FastAPI + uvicorn backend with `/api/*` routes.
- Same-port web UI served by the Python app.
- MCP-compatible JSON-RPC endpoint at `/mcp`.
- Filesystem-only persistence.
- Strict storage split:
  - market data root: public/readable cache for quotes and trading calendar
  - project root: private agent config, orders, fills, positions, equity history
- Agent registration with initial cash, token header, token secret, and T+1 sell constraint.
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
config/app.json
var/
  market-data/
    quotes/
    daily-bars/
    calendar/
  project/
    config/
      agents.json
    agents/
      <agent_id>/
        state.json
```

`var/market-data` is the root intended to be shared read-only with other users or agents. `var/project` is private application state and should stay unreadable to other users at the OS level.

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
```

Build from the project root:

```bash
./build-frontend.sh
```

This keeps `quant_arena/` as Python source only. Static assets are intentionally outside the package tree now.

## Configuration

`config/app.json`:

```json
{
	"host": "127.0.0.1",
	"port": 18792,
	"timezone": "Asia/Shanghai",
	"project_root": "./var/project",
	"market_data_root": "./var/market-data",
	"polling_interval_seconds": 300,
	"enable_background_polling": true,
	"fees": {
		"commission_bps": 3.0,
		"min_commission": 5.0,
		"stamp_tax_bps": 10.0
	}
}
```

Keep the two roots on separate filesystem paths if you want stronger permission boundaries.

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
- `POST /mcp`

## MCP authentication

Each agent authenticates with its own configured header name and token secret. Example:

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

Supported MCP calls in this implementation:

- `initialize`
- `resources/list`
- `resources/read`
- `tools/list`
- `tools/call`

Tools:

- `get_portfolio`
- `list_operations`
- `submit_operation`

## Notes

- `baostock` is loaded lazily at runtime. Install it in the environment before using live data.
- The current UI is intentionally thin and same-port. It exposes the key admin flows without introducing a separate reverse proxy.
- Historical daily bars are not populated yet; the public `daily-bars/` directory is reserved for that next step.

## Tests

```bash
uv run pytest
```
