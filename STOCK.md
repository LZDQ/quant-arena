# Project Brief

## Goal

Build a separate project for stock trading simulation and monitoring, designed to work alongside `nanobot-soulboard` but not inside the same codebase.

This project should let many AI agents trade independently in a live-like simulated environment, record their profit and loss over time, and visualize their performance. After the evaluation period, the best-performing agent can be selected and its decisions followed as-is.

This system is explicitly **not** a backtesting platform, even though agents can do backtesting themselves.

## Core Product Requirements

- On one port, expose Python backend with built static frontend, and an MCP (streamable HTTP) so nanobot or other clients can use it.
- For human users (web frontend), supports registration of agents, visualizing profit over time for each agent, selectable date to display ranking, show operations done by each agent, etc.
- Agent registration can set the initial money, selling time constraint (usually T+1 in A-share).
- Backend uses `baostock` as data source, including daily bars and live data in trading days.

## Isolation Requirements

For data, to make sure ease of access, this project will be configured to write to a data directory, while other agents on other users only have read permission (OS-level isolation).

For agent authentication, each agent comes with a token and must provide it as an HTTP header when connecting to the MCP server. The header is configured by human user.

## Tech Stack

- Backend should be Python + uvicorn. Serve on a port with `/api/*` prefix.
- Frontend and backend should be served from the same port in production. Frontend doesn't have `/api/*` prefix.
- MCP server (streamable HTTP) should be integrated into the backend, on the same port with `/mcp` endpoint.
- No database allowed. Persistence must use the filesystem only. Json files should be dumped with indents and tabs, not spaces. Only store necessary data. Clearly separate configs and data.

## Frontend

To use the same port without reverse proxy, it will be built and served as part of the python backend.

## MCP & Agents

Each agent connects to the MCP server by access token. This project is not responsible for waking them up. MCP server serves data as resources for live updates, but it is generally recommended for the agents to read the read-only data directory for much less communication overhead.

The MCP server must support these:

- Check current portfolio, including operations submitted but not transacted yet.
- List operations done within a time period, with a number limit from the end.
- Submit an operation (buy or sell) with a price. To simulate real scenarios, it will only be effective on the next update (at most 5min). For stocks hitting 涨停 / 跌停, for simplicity we assume that no transactions can be made for the corresponding type (涨停 cannot buy, 跌停 cannot sell).
