"""FastAPI server for quant-arena. A lot of code is deprecated and do not modify this."""

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

from quant_arena.clock import SHANGHAI_TZ, now_shanghai
from quant_arena.schemas import AgentResponse, CodeRefreshResponse, CodeSearchItem, CodeSearchResponse, CreateAgentRequest, MarketParseResponse, PathsResponse, SubmitOrderRequest, UpdateAgentRequest
from quant_arena.arena import ArenaService
from quant_arena.config import AgentConfig, AppConfig, load_app_config
from quant_arena.errors import ServiceError
from quant_arena.market import MarketService
from quant_arena.mcp_server import create_mcp_server, wrap_mcp_with_agent_auth
from quant_arena.storage import StorageService


DEFAULT_CONFIG_PATH = Path.home() / ".quant-arena" / "config.json"


class AppState:
    """Typed app state."""

    def __init__(
        self,
        config_path: Path,
        config: AppConfig,
        market: MarketService,
        arena: ArenaService,
    ):
        self.config_path = config_path
        self.config = config
        self.market = market
        self.arena = arena
        self.background_task: asyncio.Task[None] | None = None


def _load_arena(config_path: Path, market_provider: Any | None = None) -> AppState:
    config = load_app_config(config_path)
    storage = StorageService(Path(config.agents_root).resolve(), Path(config.market_data_root).resolve())
    storage.ensure_layout()
    agents = storage.load_agent_configs()
    market = market_provider or MarketService(Path(config.market_data_root).resolve())
    arena = ArenaService(config=config, storage=storage, market=market)
    arena.set_agents(agents)
    return AppState(config_path=config_path, config=config, storage=storage, market=market, arena=arena)


def _search_code_names(
    state: AppState,
    query: str = "",
    page: int = 1,
    page_size: int = 20
) -> CodeSearchResponse:
    normalized_page = max(page, 1)
    normalized_page_size = min(max(page_size, 1), 100)
    code_names = state.market.get_code_names()
    items = [] if code_names is None else [CodeSearchItem(code=row["code"], name=row["name"]) for _, row in code_names.iterrows()]
    needle = query.strip().lower()
    if needle:
        items = [item for item in items if needle in item.code.lower() or needle in item.name.lower()]
    total = len(items)
    start = (normalized_page - 1) * normalized_page_size
    end = start + normalized_page_size
    return CodeSearchResponse(
        query=query,
        page=normalized_page,
        page_size=normalized_page_size,
        total=total,
        items=items[start:end],
        last_refreshed_at=state.market.code_names_last_refreshed_at(),
        auto_refresh_enabled=state.config.enable_code_name_refresh,
    )


async def _poll_market(state: AppState) -> None:
    while True:
        state.arena.step_and_match_pending_orders()  # this inheritently parses market data
        await asyncio.sleep(state.config.polling_interval_seconds)


def create_app(config_path: Path | None = None, market_provider: Any | None = None) -> FastAPI:
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
            agents_root=str(state.storage.agents_root),
            market_data_root=str(state.storage.market_data_root),
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

    @app.post("/api/market/codes/refresh", response_model=CodeRefreshResponse)
    def refresh_market_codes() -> CodeRefreshResponse:
        return _refresh_code_names_status(get_state(), force=True)

    @app.get("/api/market/codes", response_model=CodeSearchResponse)
    def search_market_codes(query: str = "", page: int = 1, page_size: int = 20) -> CodeSearchResponse:
        return _search_code_names(get_state(), query=query, page=page, page_size=page_size)

    @app.post("/api/market/parse-today", response_model=MarketParseResponse)
    def parse_today_market() -> MarketParseResponse:
        state = get_state()
        result = state.market.finalize_market_data_for_codes_if_market_closed(_current_market_codes(state.market))
        return MarketParseResponse(**result.model_dump())

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
