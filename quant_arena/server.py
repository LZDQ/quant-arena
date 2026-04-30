"""FastAPI server for quant-arena."""

from logging import getLogger
import asyncio
import os
import secrets
from contextlib import AsyncExitStack
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from quant_arena.schemas import AgentCreatedResponse, AgentResponse, AgentSnapshotResponse, CreateAgentRequest, DailyReportPage, OperationListResponse, PathsResponse, PortfolioResponse
from quant_arena.ashare import (
    ArenaService,
    AShareService,
    create_ashare_mcp_server,
    wrap_mcp_with_agent_auth,
)
from quant_arena.config import AgentConfig, AppConfig, load_app_config
from quant_arena.errors import ServiceError
from quant_arena.ib_mcp import create_ib_mcp_server, wrap_ib_mcp_with_token_auth
from quant_arena.ib_service import IBService
from quant_arena.models import DailyReport, RankingSnapshot
from quant_arena.notifier import NotifierService
from quant_arena.napcat import NapCatNotifier
from quant_arena.qq_open import QQOpenNotifier

logger = getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".quant-arena" / "config.json"


class AppState:
    """Typed app state."""

    def __init__(
        self,
        config_path: Path,
        config: AppConfig,
        ashare_agents_root: Path,
        ashare_market_data_root: Path,
        market: AShareService,
        arena: ArenaService,
        notifier: NotifierService,
        ib_paper: IBService | None,
        ib_real: IBService | None,
    ):
        self.config_path = config_path
        self.config = config
        self.ashare_agents_root = ashare_agents_root
        self.ashare_market_data_root = ashare_market_data_root
        self.market = market
        self.arena = arena
        self.notifier = notifier
        self.ib_paper = ib_paper
        self.ib_real = ib_real
        self.background_tasks: list[asyncio.Task[None]] = []


def _load_app_state(config_path: Path, market_service: AShareService | None = None) -> AppState:
    config = load_app_config(config_path)
    ashare_root = (config_path.parent / "A-share").resolve()
    agents_root = ashare_root / "agents"
    market_data_root = Path(config.ashare.market_data_root).resolve()
    market = market_service or AShareService(market_data_root)
    notifier = NotifierService(
        napcat=NapCatNotifier(config.napcat, agents_root),
        qq_open=QQOpenNotifier(config.qq_open),
    )
    arena = ArenaService(
        agents_root=agents_root,
        market=market,
        fees=config.ashare.fees,
        notifier=notifier,
        intraday_fetch_workers=config.ashare.intraday_fetch_workers,
    )
    ib_paper: IBService | None = None
    ib_real: IBService | None = None
    if config.ib.enabled:
        ib_paper = IBService(
            mode="paper",
            connection=config.ib.paper,
            default_exchange=config.ib.default_exchange,
            default_currency=config.ib.default_currency,
            request_timeout_seconds=config.ib.request_timeout_seconds,
        )
        ib_real = IBService(
            mode="real",
            connection=config.ib.real,
            default_exchange=config.ib.default_exchange,
            default_currency=config.ib.default_currency,
            request_timeout_seconds=config.ib.request_timeout_seconds,
        )
    return AppState(
        config_path=config_path,
        config=config,
        ashare_agents_root=agents_root,
        ashare_market_data_root=market_data_root,
        market=market,
        arena=arena,
        notifier=notifier,
        ib_paper=ib_paper,
        ib_real=ib_real,
    )


def create_app(
    config_path: Path | None = None,
    market_service: AShareService | None = None
) -> FastAPI:
    """Create the FastAPI app."""

    resolved_config = (config_path or DEFAULT_CONFIG_PATH).resolve()
    mcp_server = create_ashare_mcp_server(lambda: app.state.app_state.arena)
    ib_mcp_server = create_ib_mcp_server()

    def _require_ib_paper() -> IBService:
        ib = app.state.app_state.ib_paper
        if ib is None:
            raise RuntimeError("IB integration is not enabled in config")
        return ib

    def _require_ib_real() -> IBService:
        ib = app.state.app_state.ib_real
        if ib is None:
            raise RuntimeError("IB integration is not enabled in config")
        return ib

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            state = _load_app_state(resolved_config, market_service=market_service)
            app.state.app_state = state
            await stack.enter_async_context(mcp_server.session_manager.run())
            await stack.enter_async_context(ib_mcp_server.session_manager.run())
            await state.notifier.start()
            if state.ib_paper is not None:
                state.ib_paper.start()
            if state.ib_real is not None:
                state.ib_real.start()
            if state.config.ashare.polling_interval_seconds > 0:
                state.background_tasks.append(
                    asyncio.create_task(
                        state.market.run(state.config.ashare.polling_interval_seconds)
                    )
                )
                state.background_tasks.append(
                    asyncio.create_task(
                        state.arena.run(state.config.ashare.polling_interval_seconds)
                    )
                )
            try:
                yield
            finally:
                for task in state.background_tasks:
                    task.cancel()
                for task in state.background_tasks:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                if state.ib_paper is not None:
                    state.ib_paper.close()
                if state.ib_real is not None:
                    state.ib_real.close()
                await state.notifier.close()

    app = FastAPI(title="quant-arena", lifespan=lifespan)
    base_url = os.environ.get("QUANT_ARENA_BASE_URL", "")
    api = APIRouter()

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
    app.mount(f"{base_url}/assets" if base_url else "/assets", StaticFiles(directory=static_dir / "assets", check_dir=False), name="assets")
    app.mount(
        f"{base_url}/A-share/mcp/" if base_url else "/A-share/mcp/",
        wrap_mcp_with_agent_auth(
            mcp_server.streamable_http_app(),
            lambda: app.state.app_state.arena,
        ),
    )
    app.mount(
        f"{base_url}/ib/mcp/" if base_url else "/ib/mcp/",
        wrap_ib_mcp_with_token_auth(
            ib_mcp_server.streamable_http_app(),
            load_app_config(resolved_config).ib,
            _require_ib_paper,
            _require_ib_real,
        ),
    )

    def get_state() -> AppState:
        return app.state.app_state

    def to_agent_response(agent_id: str, agent: AgentConfig) -> AgentResponse:
        return AgentResponse(
            agent_id=agent_id,
            display_name=agent.display_name,
            initial_cash=agent.initial_cash,
            enabled=agent.enabled,
            role=agent.role,
        )

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/api/paths", response_model=PathsResponse)
    def get_paths() -> PathsResponse:
        state = get_state()
        return PathsResponse(
            config_path=str(state.config_path),
            agents_root=str(state.ashare_agents_root),
            market_data_root=str(state.ashare_market_data_root),
        )

    @api.get("/api/agents")
    def list_agents() -> list[AgentResponse]:
        return [to_agent_response(agent_id, agent) for agent_id, agent in get_state().arena.list_agents()]

    @api.post("/api/agents", response_model=AgentCreatedResponse)
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

    @api.get("/api/agents/{agent_id}", response_model=AgentSnapshotResponse)
    def get_agent(agent_id: str) -> AgentSnapshotResponse:
        arena = get_state().arena
        return AgentSnapshotResponse(
            agent=to_agent_response(agent_id, arena.get_agent(agent_id)),
            portfolio=PortfolioResponse.model_validate(arena.get_portfolio(agent_id).model_dump(mode="json")),
            operations=OperationListResponse.model_validate(arena.list_operations(agent_id).model_dump(mode="json")),
            equity=arena.get_equity_curve(agent_id),
        )

    @api.delete("/api/agents/{agent_id}", status_code=204)
    def delete_agent(agent_id: str) -> None:
        get_state().arena.delete_agent(agent_id)

    @api.get("/api/agents/{agent_id}/daily-reports", response_model=DailyReportPage)
    def list_daily_reports(agent_id: str, page: int = 1, page_size: int = 20) -> DailyReportPage:
        items, total = get_state().arena.list_daily_reports(agent_id, page=page, page_size=page_size)
        return DailyReportPage(items=items, total=total, page=page, page_size=page_size)

    @api.get("/api/agents/{agent_id}/daily-reports/{trade_date}", response_model=DailyReport)
    def get_daily_report(agent_id: str, trade_date: date) -> DailyReport:
        return get_state().arena.get_daily_report(agent_id, trade_date)

    @api.get("/api/rankings")
    def get_rankings(date_value: str | None = None) -> list[RankingSnapshot]:
        target_date = date.fromisoformat(date_value) if date_value else None
        return get_state().arena.get_rankings(target_date)

    @api.api_route("/A-share/mcp", methods=["GET", "POST", "DELETE"])
    def mcp_redirect() -> RedirectResponse:
        target = f"{base_url}/A-share/mcp/" if base_url else "/A-share/mcp/"
        return RedirectResponse(url=target, status_code=307)

    @api.api_route("/ib/mcp", methods=["GET", "POST", "DELETE"])
    def ib_mcp_redirect() -> RedirectResponse:
        target = f"{base_url}/ib/mcp/" if base_url else "/ib/mcp/"
        return RedirectResponse(url=target, status_code=307)

    app.include_router(api, prefix=base_url)

    if base_url:
        @app.get("/")
        def root_redirect() -> RedirectResponse:
            return RedirectResponse(url=f"{base_url}/", status_code=307)

        @app.get(base_url)
        def frontend_base_redirect() -> RedirectResponse:
            return RedirectResponse(url=f"{base_url}/", status_code=307)

    @app.get(f"{base_url}/{{path:path}}" if base_url else "/{path:path}")
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
