"""FastAPI server for quant-arena."""

import asyncio
from contextlib import AsyncExitStack
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from quant_arena.arena import ArenaService
from quant_arena.config import AgentConfig, AppConfig, load_app_config
from quant_arena.market import BaoStockMarketDataProvider, MarketDataProvider
from quant_arena.mcp_server import create_mcp_server, wrap_mcp_with_agent_auth
from quant_arena.models import CreateAgentRequest, MarketBarsResponse, MarketParseResponse, MarketStatusResponse, PathsResponse, SubmitOrderRequest, UpdateAgentRequest
from quant_arena.storage import ArenaStorage


DEFAULT_CONFIG_PATH = Path.home() / ".quant-arena" / "config.json"


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
	storage = ArenaStorage(Path(config.agents_root).resolve(), Path(config.market_data_root).resolve())
	storage.ensure_layout()
	agents = storage.load_agent_configs()
	arena = ArenaService(config=config, storage=storage, market_data=market_provider or BaoStockMarketDataProvider())
	arena.set_agents(agents)
	return AppState(config_path=config_path, config=config, storage=storage, arena=arena)


async def _poll_market(state: AppState) -> None:
	while True:
		state.arena.sync_market_data()
		state.arena.match_pending_orders()
		await asyncio.sleep(state.config.polling_interval_seconds)


def create_app(config_path: Path | None = None, market_provider: MarketDataProvider | None = None) -> FastAPI:
	"""Create the FastAPI app."""

	resolved_config = (config_path or DEFAULT_CONFIG_PATH).resolve()
	mcp_server = create_mcp_server(lambda: app.state.ctx.arena)

	@asynccontextmanager
	async def lifespan(app: FastAPI):
		async with AsyncExitStack() as stack:
			state = _load_arena(resolved_config, market_provider=market_provider)
			app.state.ctx = state
			await stack.enter_async_context(mcp_server.session_manager.run())
			if state.config.enable_background_polling and state.config.polling_interval_seconds > 0:
				state.background_task = asyncio.create_task(_poll_market(state))
			try:
				yield
			finally:
				if state.background_task is not None:
					state.background_task.cancel()
					try:
						await state.background_task
					except asyncio.CancelledError:
						pass

	app = FastAPI(title="quant-arena", lifespan=lifespan)
	app.add_middleware(
		CORSMiddleware,
		allow_origins=["*"],
		allow_methods=["*"],
		allow_headers=["*"],
	)
	static_dir = Path(__file__).resolve().parent.parent / "static"
	app.mount("/assets", StaticFiles(directory=static_dir / "assets", check_dir=False), name="assets")
	app.mount("/mcp/", wrap_mcp_with_agent_auth(mcp_server.streamable_http_app(), lambda: app.state.ctx.arena))

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
			agents_root=str(state.storage.agents_root),
			market_data_root=str(state.storage.market_data_root),
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
		get_state().arena.sync_market_data()
		get_state().arena.match_pending_orders()
		return {"status": "ok"}

	@app.post("/api/market/parse-today", response_model=MarketParseResponse)
	def parse_today_market() -> MarketParseResponse:
		return get_state().arena.parse_today_market_data_if_missing()

	@app.get("/api/market/status", response_model=MarketStatusResponse)
	def get_market_status() -> MarketStatusResponse:
		return get_state().arena.get_market_status()

	@app.get("/api/market/bars", response_model=MarketBarsResponse)
	def get_market_bars(code: str, trade_date: str | None = None) -> MarketBarsResponse:
		parsed_trade_date = date.fromisoformat(trade_date) if trade_date else None
		return get_state().arena.get_market_bars(code, parsed_trade_date)

	@app.get("/api/rankings")
	def get_rankings(date_value: str | None = None) -> Any:
		target_date = date.fromisoformat(date_value) if date_value else None
		return get_state().arena.get_rankings(target_date)

	@app.api_route("/mcp", methods=["GET", "POST", "DELETE"])
	def mcp_redirect() -> RedirectResponse:
		return RedirectResponse(url="/mcp/", status_code=307)

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

	config_path = DEFAULT_CONFIG_PATH.resolve()
	config = load_app_config(config_path)
	uvicorn.run("quant_arena.server:create_app", host=config.host, port=config.port, factory=True)
