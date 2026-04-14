"""FastAPI server for quant-arena."""

from logging import getLogger
import asyncio
import secrets
from contextlib import AsyncExitStack
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from quant_arena.schemas import AgentCreatedResponse, AgentResponse, AgentSnapshotResponse, CodeRefreshResponse, CodeSearchItem, CodeSearchResponse, CreateAgentRequest, OperationListResponse, PathsResponse, PortfolioResponse
from quant_arena.arena import ArenaService
from quant_arena.config import AgentConfig, AppConfig, load_app_config
from quant_arena.errors import ServiceError
from quant_arena.market import MarketService
from quant_arena.mcp_server import create_mcp_server, wrap_mcp_with_agent_auth
from quant_arena.models import RankingSnapshot
from quant_arena.clock import now_shanghai

logger = getLogger(__name__)

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


def _load_app_state(config_path: Path, market_service: MarketService | None = None) -> AppState:
    config = load_app_config(config_path)
    market = market_service or MarketService(Path(config.market_data_root).resolve())
    arena = ArenaService(
        agents_root=Path(config.agents_root).resolve(),
        market=market,
        fees=config.fees,
    )
    return AppState(config_path=config_path, config=config, market=market, arena=arena)


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
        last_refreshed_at=None,
        auto_refresh_enabled=state.config.enable_code_name_refresh,
    )


async def _poll_market(state: AppState) -> None:
    """
    Poll market data.

    From 9:30 to 15:00, poll intraday and match orders.
    After 17:30, finalize today's daily bars using baostock.
    After 20:00, finalize today's 5min bars using baostock.
    Note that do not use multiple workers or restart the
    server frequently when finalizing.
    TODO: implement built-in continuation of finalization
    """
    last_refreshed_date: date | None = None
    last_finalized_daily_date: date | None = None
    last_finalized_5min_date: date | None = None
    is_trading_day = True
    while True:
        now = now_shanghai()
        today = now.date()
        if last_refreshed_date != today:
            logger.debug("Refreshing today's trading status")
            last_refreshed_date = today
            trade_date_frame = state.market.fetch_trade_dates(today, today)
            if not trade_date_frame.empty:
                is_trading_day = str(trade_date_frame.iloc[-1]["is_trading_day"]) == "1"
                logger.info("Today's trading status is: %r", is_trading_day)
            else:
                logger.error("Cannot fetch today's trading status. Defaulting to False")
                is_trading_day = False

            if not is_trading_day:
                tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo)
                await asyncio.sleep(max((tomorrow - now).total_seconds(), 0.0))
                continue

        if now.time() >= time(17, 30) and last_finalized_daily_date != today:
            try:
                await asyncio.to_thread(state.market.finalize_market_data_daily, today)
            except Exception:
                logger.exception("Exception in finalizing today's daily bars")
            last_finalized_daily_date = today

        if now.time() >= time(20, 0) and last_finalized_5min_date != today:
            try:
                await asyncio.to_thread(state.market.finalize_market_data_5min, today)
            except Exception:
                logger.exception("Exception in finalizing today's 5min bars")
            last_finalized_5min_date = today

        if time(9, 30) <= now.time() <= time(15,00):
            try:
                await asyncio.to_thread(state.arena.match_pending_orders)
            except Exception:
                logger.exception("Exception in matching pending orders")

        await asyncio.sleep(state.config.polling_interval_seconds)


def create_app(
    config_path: Path | None = None,
    market_service: MarketService | None = None
) -> FastAPI:
    """Create the FastAPI app."""

    resolved_config = (config_path or DEFAULT_CONFIG_PATH).resolve()
    mcp_server = create_mcp_server(lambda: app.state.app_state.arena)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            state = _load_app_state(resolved_config, market_service=market_service)
            app.state.app_state = state
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
    app.mount(
        "/mcp/",
        wrap_mcp_with_agent_auth(
            mcp_server.streamable_http_app(),
            lambda: app.state.app_state.arena,
        ),
    )

    def get_state() -> AppState:
        return app.state.app_state

    def to_agent_response(agent_id: str, agent: AgentConfig) -> AgentResponse:
        return AgentResponse(
            agent_id=agent_id,
            display_name=agent.display_name,
            initial_cash=agent.initial_cash,
            sell_constraint=agent.sell_constraint,
            enabled=agent.enabled,
            role=agent.role,
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/paths", response_model=PathsResponse)
    def get_paths() -> PathsResponse:
        state = get_state()
        return PathsResponse(
            config_path=str(state.config_path),
            agents_root=state.config.agents_root,
            market_data_root=state.config.market_data_root,
        )

    @app.get("/api/agents")
    def list_agents() -> list[AgentResponse]:
        return [to_agent_response(agent_id, agent) for agent_id, agent in get_state().arena.list_agents()]

    @app.post("/api/agents", response_model=AgentCreatedResponse)
    def create_agent(request: CreateAgentRequest) -> AgentCreatedResponse:
        token_secret = secrets.token_urlsafe(24)
        agent = AgentConfig.model_validate(
            {
                **request.model_dump(exclude={"agent_id"}),
                "token_secret": token_secret,
            }
        )
        created = get_state().arena.add_agent(request.agent_id, agent)
        return AgentCreatedResponse(
            agent=to_agent_response(request.agent_id, created),
            token_secret=token_secret,
        )

    @app.get("/api/agents/{agent_id}", response_model=AgentSnapshotResponse)
    def get_agent(agent_id: str) -> AgentSnapshotResponse:
        arena = get_state().arena
        return AgentSnapshotResponse(
            agent=to_agent_response(agent_id, arena.get_agent(agent_id)),
            portfolio=PortfolioResponse.model_validate(arena.get_portfolio(agent_id).model_dump(mode="json")),
            operations=OperationListResponse.model_validate(arena.list_operations(agent_id).model_dump(mode="json")),
            equity=arena.get_equity_curve(agent_id),
        )

    @app.delete("/api/agents/{agent_id}", status_code=204)
    def delete_agent(agent_id: str) -> None:
        get_state().arena.delete_agent(agent_id)

    @app.post("/api/market/codes/refresh", response_model=CodeRefreshResponse)
    def refresh_market_codes() -> CodeRefreshResponse:
        state = get_state()
        state.market.refresh_code_names()
        code_names = state.market.get_code_names()
        return CodeRefreshResponse(
            refreshed_at=now_shanghai(),
            entry_count=0 if code_names is None else len(code_names),
        )

    @app.get("/api/market/codes", response_model=CodeSearchResponse)
    def search_market_codes(query: str = "", page: int = 1, page_size: int = 20) -> CodeSearchResponse:
        return _search_code_names(get_state(), query=query, page=page, page_size=page_size)

    @app.get("/api/rankings")
    def get_rankings(date_value: str | None = None) -> list[RankingSnapshot]:
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
