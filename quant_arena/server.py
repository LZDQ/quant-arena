"""FastAPI server for quant-arena."""

import asyncio
from contextlib import AsyncExitStack
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from quant_arena.schemas import AgentResponse, CodeRefreshResponse, CodeSearchResponse, CreateAgentRequest, MarketBarsResponse, MarketParseJobResponse, MarketParseResponse, MarketRangeParseRequest, MarketStatusResponse, PathsResponse, SubmitOrderRequest, UpdateAgentRequest
from quant_arena.arena import ArenaService
from quant_arena.config import AgentConfig, AppConfig, load_app_config
from quant_arena.errors import ServiceError
from quant_arena.market import BaoStockMarketDataProvider, MarketDataProvider, MarketService
from quant_arena.mcp_server import create_mcp_server, wrap_mcp_with_agent_auth
from quant_arena.storage import StorageService


DEFAULT_CONFIG_PATH = Path.home() / ".quant-arena" / "config.json"


class AppState:
    """Typed app state."""

    def __init__(self, config_path: Path, config: AppConfig, storage_service: StorageService, market: MarketService, arena: ArenaService):
        self.config_path = config_path
        self.config = config
        self.storage_service = storage_service
        self.market = market
        self.arena = arena
        self.background_task: asyncio.Task[None] | None = None


def _load_arena(config_path: Path, market_provider: MarketDataProvider | None = None) -> AppState:
    config = load_app_config(config_path)
    storage_service = StorageService(Path(config.agents_root).resolve(), Path(config.market_data_root).resolve())
    storage_service.ensure_layout()
    agents = storage_service.load_agent_configs()
    market = MarketService(config=config, storage_service=storage_service, provider=market_provider or BaoStockMarketDataProvider())
    arena = ArenaService(config=config, storage_service=storage_service, market=market)
    arena.set_agents(agents)
    return AppState(config_path=config_path, config=config, storage_service=storage_service, market=market, arena=arena)


async def _poll_market(state: AppState) -> None:
    while True:
        state.market.sync_market_data(state.market.tracked_codes())
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
                await state.market.shutdown()
                if state.background_task is not None:
                    state.background_task.cancel()
                    try:
                        await state.background_task
                    except asyncio.CancelledError:
                        pass

    app = FastAPI(title="quant-arena", lifespan=lifespan)

    @app.exception_handler(ServiceError)
    async def handle_service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

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

    def to_agent_response(agent_id: str, agent: AgentConfig) -> AgentResponse:
        return AgentResponse(agent_id=agent_id, **agent.model_dump())

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/paths", response_model=PathsResponse)
    def get_paths() -> PathsResponse:
        state = get_state()
        return PathsResponse(
            config_path=str(state.config_path),
            agents_root=str(state.storage_service.agents_root),
            market_data_root=str(state.storage_service.market_data_root),
        )

    @app.get("/api/agents")
    def list_agents() -> list[AgentResponse]:
        return [to_agent_response(agent_id, agent) for agent_id, agent in get_state().arena.list_agent_items()]

    @app.post("/api/agents", response_model=AgentResponse)
    def create_agent(request: CreateAgentRequest) -> AgentResponse:
        agent = AgentConfig.model_validate(request.model_dump(exclude={"agent_id"}))
        created = get_state().arena.add_agent(request.agent_id, agent)
        return to_agent_response(request.agent_id, created)

    @app.get("/api/agents/{agent_id}", response_model=AgentResponse)
    def get_agent(agent_id: str) -> AgentResponse:
        return to_agent_response(agent_id, get_state().arena.get_agent(agent_id))

    @app.patch("/api/agents/{agent_id}", response_model=AgentResponse)
    def update_agent(agent_id: str, request: UpdateAgentRequest) -> AgentResponse:
        updated = get_state().arena.update_agent(agent_id, request.model_dump())
        return to_agent_response(agent_id, updated)

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
        state = get_state()
        state.market.sync_market_data(state.market.tracked_codes())
        get_state().arena.match_pending_orders()
        return {"status": "ok"}

    @app.post("/api/market/codes/refresh", response_model=CodeRefreshResponse)
    def refresh_market_codes() -> CodeRefreshResponse:
        return get_state().market.refresh_code_names(force=True)

    @app.get("/api/market/codes", response_model=CodeSearchResponse)
    def search_market_codes(query: str = "", page: int = 1, page_size: int = 20) -> CodeSearchResponse:
        return get_state().market.search_code_names(query=query, page=page, page_size=page_size)

    @app.post("/api/market/parse-today", response_model=MarketParseResponse)
    def parse_today_market() -> MarketParseResponse:
        state = get_state()
        return state.market.parse_today_market_data_if_missing(state.market.tracked_codes())

    @app.post("/api/market/parse-jobs", response_model=MarketParseJobResponse)
    async def start_market_parse_job(request: MarketRangeParseRequest) -> MarketParseJobResponse:
        state = get_state()
        return await state.market.start_range_parse_job(state.market.tracked_codes(), request)

    @app.get("/api/market/parse-jobs", response_model=list[MarketParseJobResponse])
    async def list_market_parse_jobs() -> list[MarketParseJobResponse]:
        return await get_state().market.list_parse_jobs()

    @app.get("/api/market/status", response_model=MarketStatusResponse)
    def get_market_status() -> MarketStatusResponse:
        state = get_state()
        return state.market.get_market_status(state.market.tracked_codes())

    @app.get("/api/market/bars", response_model=MarketBarsResponse)
    def get_market_bars(code: str, trade_date: str | None = None) -> MarketBarsResponse:
        parsed_trade_date = date.fromisoformat(trade_date) if trade_date else None
        return get_state().market.get_market_bars(code, parsed_trade_date)

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
