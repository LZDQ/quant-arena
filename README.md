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

Tools:

- `get_portfolio`
- `list_operations`
- `submit_operation`

## Soulboard Integration

Preconfigured agent prompts for `nanobot-soulboard` are under `quant_arena/resources/soulboard/`. Copy those markdown files to a workspace and make the agent trade.

## Tests

```bash
uv run pytest
```
