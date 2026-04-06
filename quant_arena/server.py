"""FastAPI server for quant-arena."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from quant_arena.arena import ArenaService
from quant_arena.config import AgentConfig, AppConfig, load_agents_config, load_app_config
from quant_arena.market import BaoStockMarketDataProvider, MarketDataProvider
from quant_arena.models import CreateAgentRequest, MCPRequest, MCPResponse, PathsResponse, SubmitOrderRequest, UpdateAgentRequest
from quant_arena.storage import ArenaStorage


class AppState:
	"""Typed app state."""

	def __init__(self, config_path: Path, config: AppConfig, storage: ArenaStorage, arena: ArenaService):
		self.config_path = config_path
		self.config = config
		self.storage = storage
		self.arena = arena
		self.background_task: asyncio.Task[None] | None = None


def _load_arena(config_path: Path, market_provider: MarketDataProvider | None = None) -> AppState:
	config = load_app_config(config_path)
	storage = ArenaStorage(Path(config.project_root).resolve(), Path(config.market_data_root).resolve())
	storage.ensure_layout()
	agents = load_agents_config(storage.agents_config_path())
	arena = ArenaService(config=config, storage=storage, market_data=market_provider or BaoStockMarketDataProvider())
	arena.set_agents(agents)
	return AppState(config_path=config_path, config=config, storage=storage, arena=arena)


async def _poll_market(state: AppState) -> None:
	while True:
		state.arena.match_pending_orders()
		await asyncio.sleep(state.config.polling_interval_seconds)


def create_app(config_path: Path | None = None, market_provider: MarketDataProvider | None = None) -> FastAPI:
	"""Create the FastAPI app."""

	resolved_config = (config_path or Path("./config/app.json")).resolve()

	@asynccontextmanager
	async def lifespan(app: FastAPI):
		state = _load_arena(resolved_config, market_provider=market_provider)
		app.state.ctx = state
		if state.config.enable_background_polling and state.config.polling_interval_seconds > 0:
			state.background_task = asyncio.create_task(_poll_market(state))
		yield
		if state.background_task is not None:
			state.background_task.cancel()
			try:
				await state.background_task
			except asyncio.CancelledError:
				pass

	app = FastAPI(title="quant-arena", lifespan=lifespan)
	static_dir = resolved_config.parent.parent / "static"
	app.mount("/assets", StaticFiles(directory=static_dir / "assets", check_dir=False), name="assets")

	def get_state() -> AppState:
		return app.state.ctx

	@app.get("/health")
	def health() -> dict[str, str]:
		return {"status": "ok"}

	@app.get("/api/paths", response_model=PathsResponse)
	def get_paths() -> PathsResponse:
		state = get_state()
		return PathsResponse(
			config_path=str(state.config_path),
			project_root=str(state.storage.project_root),
			market_data_root=str(state.storage.market_data_root),
			agents_config_path=str(state.storage.agents_config_path()),
		)

	@app.get("/api/agents")
	def list_agents() -> list[AgentConfig]:
		return get_state().arena.list_agents()

	@app.post("/api/agents", response_model=AgentConfig)
	def create_agent(request: CreateAgentRequest) -> AgentConfig:
		return get_state().arena.add_agent(AgentConfig.model_validate(request.model_dump()))

	@app.get("/api/agents/{agent_id}", response_model=AgentConfig)
	def get_agent(agent_id: str) -> AgentConfig:
		return get_state().arena.get_agent(agent_id)

	@app.patch("/api/agents/{agent_id}", response_model=AgentConfig)
	def update_agent(agent_id: str, request: UpdateAgentRequest) -> AgentConfig:
		return get_state().arena.update_agent(agent_id, request.model_dump())

	@app.delete("/api/agents/{agent_id}", status_code=204)
	def delete_agent(agent_id: str) -> None:
		get_state().arena.delete_agent(agent_id)

	@app.get("/api/agents/{agent_id}/portfolio")
	def get_portfolio(agent_id: str) -> Any:
		return get_state().arena.get_portfolio(agent_id)

	@app.get("/api/agents/{agent_id}/operations")
	def get_operations(agent_id: str, start: str | None = None, end: str | None = None, limit: int | None = None) -> Any:
		parsed_start = datetime.fromisoformat(start) if start else None
		parsed_end = datetime.fromisoformat(end) if end else None
		return get_state().arena.list_operations(agent_id, start=parsed_start, end=parsed_end, limit=limit)

	@app.get("/api/agents/{agent_id}/equity")
	def get_equity(agent_id: str, start: str | None = None, end: str | None = None) -> Any:
		parsed_start = date.fromisoformat(start) if start else None
		parsed_end = date.fromisoformat(end) if end else None
		return get_state().arena.get_equity_curve(agent_id, start=parsed_start, end=parsed_end)

	@app.post("/api/agents/{agent_id}/orders")
	def submit_order(agent_id: str, request: SubmitOrderRequest) -> Any:
		return get_state().arena.submit_order(agent_id, request)

	@app.post("/api/agents/{agent_id}/orders/{order_id}/cancel")
	def cancel_order(agent_id: str, order_id: str) -> Any:
		return get_state().arena.cancel_order(agent_id, order_id)

	@app.post("/api/market/refresh")
	def refresh_market() -> dict[str, str]:
		get_state().arena.match_pending_orders()
		return {"status": "ok"}

	@app.get("/api/rankings")
	def get_rankings(date_value: str | None = None) -> Any:
		target_date = date.fromisoformat(date_value) if date_value else None
		return get_state().arena.get_rankings(target_date)

	@app.post("/mcp", response_model=MCPResponse)
	async def mcp_endpoint(request: Request) -> MCPResponse:
		state = get_state()
		headers = {key.lower(): value for key, value in request.headers.items()}
		agent = state.arena.authenticate_agent(headers)
		payload = MCPRequest.model_validate(await request.json())
		params = payload.params or {}
		try:
			if payload.method == "initialize":
				result = {
					"serverInfo": {"name": "quant-arena", "version": "0.1.0"},
					"capabilities": {"resources": {}, "tools": {}},
				}
			elif payload.method == "resources/list":
				result = {
					"resources": [
						{"uri": "arena://portfolio", "name": "Current portfolio", "mimeType": "application/json"},
						{"uri": "arena://operations", "name": "Recent operations", "mimeType": "application/json"},
					]
				}
			elif payload.method == "resources/read":
				uri = params.get("uri")
				if uri == "arena://portfolio":
					content = state.arena.get_portfolio(agent.agent_id).model_dump(mode="json")
				elif uri == "arena://operations":
					content = state.arena.list_operations(agent.agent_id, limit=50).model_dump(mode="json")
				else:
					raise HTTPException(status_code=404, detail=f"Unknown resource: {uri}")
				result = {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(content, ensure_ascii=False)}]}
			elif payload.method == "tools/list":
				result = {
					"tools": [
						{
							"name": "get_portfolio",
							"description": "Get current portfolio including pending orders.",
							"inputSchema": {"type": "object", "properties": {}},
						},
						{
							"name": "list_operations",
							"description": "List orders and fills.",
							"inputSchema": {
								"type": "object",
								"properties": {"limit": {"type": "integer", "minimum": 1}},
							},
						},
						{
							"name": "submit_operation",
							"description": "Submit a pending buy or sell order.",
							"inputSchema": {
								"type": "object",
								"required": ["symbol", "side", "quantity", "limit_price"],
								"properties": {
									"symbol": {"type": "string"},
									"side": {"type": "string", "enum": ["buy", "sell"]},
									"quantity": {"type": "integer", "minimum": 1},
									"limit_price": {"type": "number", "exclusiveMinimum": 0},
								},
							},
						},
					]
				}
			elif payload.method == "tools/call":
				name = params.get("name")
				arguments = params.get("arguments") or {}
				if name == "get_portfolio":
					content = state.arena.get_portfolio(agent.agent_id).model_dump(mode="json")
				elif name == "list_operations":
					content = state.arena.list_operations(agent.agent_id, limit=arguments.get("limit")).model_dump(mode="json")
				elif name == "submit_operation":
					order = state.arena.submit_order(agent.agent_id, SubmitOrderRequest.model_validate(arguments))
					content = order.model_dump(mode="json")
				else:
					raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")
				result = {"content": [{"type": "json", "json": content}]}
			else:
				raise HTTPException(status_code=404, detail=f"Unknown MCP method: {payload.method}")
			return MCPResponse(id=payload.id, result=result)
		except HTTPException as exc:
			return MCPResponse(id=payload.id, error={"code": exc.status_code, "message": exc.detail})

	@app.get("/{path:path}")
	def frontend(path: str):
		candidate = static_dir / path
		if path and candidate.is_file():
			return FileResponse(candidate)
		index_path = static_dir / "index.html"
		if index_path.is_file():
			return FileResponse(index_path)
		return JSONResponse(status_code=404, content={"detail": "Frontend has not been built yet"})

	return app


def run() -> None:
	"""Run the uvicorn server."""

	config_path = Path("./config/app.json").resolve()
	config = load_app_config(config_path)
	uvicorn.run("quant_arena.server:create_app", host=config.host, port=config.port, factory=True)
