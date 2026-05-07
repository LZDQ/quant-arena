"""FastAPI server for quant-arena."""

from logging import getLogger
import asyncio
import os
import secrets
from contextlib import AsyncExitStack
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Callable

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from quant_arena.schemas import AgentCreatedResponse, AgentResponse, AgentSnapshotResponse, ArenaStatus, CreateAgentRequest, DailyReportPage, OperationListResponse, PathsResponse, PortfolioResponse, ToggleArenaRequest, ToggleArenaResponse
from quant_arena.arena_base import BaseArenaService
from quant_arena.ashare import (
    ArenaService,
    AShareService,
    create_ashare_mcp_server,
    wrap_mcp_with_agent_auth,
)
from quant_arena.config import AgentConfig, AppConfig, load_app_config, save_app_config
from quant_arena.errors import BadRequestError, ServiceError
from quant_arena.futumoo import (
    FutumooArenaService,
    FutumooService,
    create_futumoo_mcp_server,
    wrap_futumoo_mcp_with_agent_auth,
)
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
        market: AShareService | None,
        arena: ArenaService | None,
        futumoo_agents_root: Path,
        futumoo_market: FutumooService | None,
        futumoo_arena: FutumooArenaService | None,
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
        self.futumoo_agents_root = futumoo_agents_root
        self.futumoo_market = futumoo_market
        self.futumoo_arena = futumoo_arena
        self.notifier = notifier
        self.ib_paper = ib_paper
        self.ib_real = ib_real
        self.background_tasks: list[asyncio.Task[None]] = []


def _load_app_state(config_path: Path, market_service: AShareService | None = None) -> AppState:
    config = load_app_config(config_path)
    ashare_root = (config_path.parent / "A-share").resolve()
    agents_root = ashare_root / "agents"
    market_data_root = Path(config.ashare.market_data_root).resolve()
    notifier = NotifierService(
        napcat=NapCatNotifier(config.napcat, agents_root),
        qq_open=QQOpenNotifier(config.qq_open),
    )
    market: AShareService | None = None
    arena: ArenaService | None = None
    if config.ashare.enabled:
        market = market_service or AShareService(market_data_root)
        arena = ArenaService(
            agents_root=agents_root,
            market=market,
            fees=config.ashare.fees,
            notifier=notifier,
            intraday_fetch_workers=config.ashare.intraday_fetch_workers,
        )
    futumoo_root = (config_path.parent / "futumoo").resolve()
    futumoo_agents_root = futumoo_root / "agents"
    futumoo_market: FutumooService | None = None
    futumoo_arena: FutumooArenaService | None = None
    if config.futumoo.enabled:
        futumoo_market = FutumooService(host=config.futumoo.host, port=config.futumoo.port)
        futumoo_arena = FutumooArenaService(
            agents_root=futumoo_agents_root,
            market=futumoo_market,
            config=config.futumoo,
            notifier=notifier,
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
        futumoo_agents_root=futumoo_agents_root,
        futumoo_market=futumoo_market,
        futumoo_arena=futumoo_arena,
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
    bootstrap_config = load_app_config(resolved_config)
    ashare_enabled = bootstrap_config.ashare.enabled
    futumoo_enabled = bootstrap_config.futumoo.enabled
    mcp_server = create_ashare_mcp_server(lambda: app.state.app_state.arena) if ashare_enabled else None
    futumoo_mcp_server = create_futumoo_mcp_server(lambda: app.state.app_state.futumoo_arena) if futumoo_enabled else None
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
            if mcp_server is not None:
                await stack.enter_async_context(mcp_server.session_manager.run())
            if futumoo_mcp_server is not None:
                await stack.enter_async_context(futumoo_mcp_server.session_manager.run())
            await stack.enter_async_context(ib_mcp_server.session_manager.run())
            await state.notifier.start()
            if state.ib_paper is not None:
                state.ib_paper.start()
            if state.ib_real is not None:
                state.ib_real.start()
            if (
                state.arena is not None
                and state.market is not None
                and state.config.ashare.polling_interval_seconds > 0
            ):
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
            if (
                state.futumoo_arena is not None
                and state.config.futumoo.polling_interval_seconds > 0
            ):
                state.background_tasks.append(
                    asyncio.create_task(
                        state.futumoo_arena.run(
                            state.config.futumoo.polling_interval_seconds
                        )
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
                if state.futumoo_market is not None:
                    state.futumoo_market.close()
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
    if mcp_server is not None:
        app.mount(
            f"{base_url}/A-share/mcp/" if base_url else "/A-share/mcp/",
            wrap_mcp_with_agent_auth(
                mcp_server.streamable_http_app(),
                lambda: app.state.app_state.arena,
            ),
        )
    if futumoo_mcp_server is not None:
        app.mount(
            f"{base_url}/futumoo/mcp/" if base_url else "/futumoo/mcp/",
            wrap_futumoo_mcp_with_agent_auth(
                futumoo_mcp_server.streamable_http_app(),
                lambda: app.state.app_state.futumoo_arena,
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
            currency=agent.currency,
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

    _ARENA_LABELS: dict[str, str] = {"ashare": "A-Share", "futumoo": "Futu Moo"}

    def _arena_statuses(config: AppConfig) -> list[ArenaStatus]:
        return [
            ArenaStatus(slug="ashare", label=_ARENA_LABELS["ashare"], enabled=config.ashare.enabled),
            ArenaStatus(slug="futumoo", label=_ARENA_LABELS["futumoo"], enabled=config.futumoo.enabled),
        ]

    @api.get("/api/arenas", response_model=list[ArenaStatus])
    def list_arenas() -> list[ArenaStatus]:
        return _arena_statuses(get_state().config)

    @api.patch("/api/arenas/{slug}", response_model=ToggleArenaResponse)
    def toggle_arena(slug: str, request: ToggleArenaRequest) -> ToggleArenaResponse:
        if slug not in _ARENA_LABELS:
            raise BadRequestError(f"Unknown arena slug {slug!r}")
        state = get_state()
        config = state.config
        if slug == "ashare":
            config.ashare.enabled = request.enabled
        else:
            config.futumoo.enabled = request.enabled
        save_app_config(state.config_path, config)
        status = ArenaStatus(slug=slug, label=_ARENA_LABELS[slug], enabled=request.enabled)
        return ToggleArenaResponse(status=status, restart_required=True)

    def _register_arena_routes(prefix: str, get_arena: Callable[[], BaseArenaService]) -> None:
        """Register the standard /agents/{,/...}/rankings endpoints for one arena.

        Mounts the same routes that A-share has historically exposed under
        `/api/agents/...` against any `BaseArenaService`. The first call
        (with prefix `""`) preserves the legacy A-share paths; later calls
        attach the same handlers under a per-broker prefix like `/futumoo`.
        The create-agent POST is registered separately by the caller because
        per-broker request shapes differ (CNY single-currency for A-share,
        HKD+USD dual-currency for Futumoo).
        """

        @api.get(f"/api{prefix}/agents")
        def list_arena_agents() -> list[AgentResponse]:
            return [
                to_agent_response(agent_id, agent)
                for agent_id, agent in get_arena().list_agents()
            ]

        @api.get(f"/api{prefix}/agents/{{agent_id}}", response_model=AgentSnapshotResponse)
        def get_arena_agent(agent_id: str) -> AgentSnapshotResponse:
            arena = get_arena()
            return AgentSnapshotResponse(
                agent=to_agent_response(agent_id, arena.get_agent(agent_id)),
                portfolio=PortfolioResponse.model_validate(
                    arena.get_portfolio(agent_id).model_dump(mode="json")
                ),
                operations=OperationListResponse.model_validate(
                    arena.list_operations(agent_id).model_dump(mode="json")
                ),
                equity=arena.get_equity_curve(agent_id),
            )

        @api.delete(f"/api{prefix}/agents/{{agent_id}}", status_code=204)
        def delete_arena_agent(agent_id: str) -> None:
            get_arena().delete_agent(agent_id)

        @api.get(
            f"/api{prefix}/agents/{{agent_id}}/daily-reports",
            response_model=DailyReportPage,
        )
        def list_arena_daily_reports(
            agent_id: str, page: int = 1, page_size: int = 20
        ) -> DailyReportPage:
            items, total = get_arena().list_daily_reports(
                agent_id, page=page, page_size=page_size
            )
            return DailyReportPage(items=items, total=total, page=page, page_size=page_size)

        @api.get(
            f"/api{prefix}/agents/{{agent_id}}/daily-reports/{{trade_date}}",
            response_model=DailyReport,
        )
        def get_arena_daily_report(agent_id: str, trade_date: date) -> DailyReport:
            return get_arena().get_daily_report(agent_id, trade_date)

        @api.get(f"/api{prefix}/rankings")
        def get_arena_rankings(date_value: str | None = None) -> list[RankingSnapshot]:
            target_date = date.fromisoformat(date_value) if date_value else None
            return get_arena().get_rankings(target_date)

    def _create_agent_handler(
        request: CreateAgentRequest,
        get_arena: Callable[[], BaseArenaService],
        allowed_currencies: tuple[str, ...],
    ) -> AgentCreatedResponse:
        if request.currency not in allowed_currencies:
            raise BadRequestError(
                f"Currency {request.currency!r} not allowed on this arena. "
                f"Choose one of {allowed_currencies}."
            )
        token_secret = secrets.token_urlsafe(24)
        agent = AgentConfig.model_validate(
            {
                **request.model_dump(exclude={"agent_id"}),
                "token_secret": token_secret,
            }
        )
        created = get_arena().add_agent(request.agent_id, agent)
        return AgentCreatedResponse(
            agent=to_agent_response(request.agent_id, created),
            token_secret=token_secret,
        )

    if ashare_enabled:
        _register_arena_routes("", lambda: get_state().arena)

        @api.post("/api/agents", response_model=AgentCreatedResponse)
        def create_ashare_agent(request: CreateAgentRequest) -> AgentCreatedResponse:
            return _create_agent_handler(request, lambda: get_state().arena, ("CNY",))

    if futumoo_enabled:
        _register_arena_routes("/futumoo", lambda: get_state().futumoo_arena)

        @api.post("/api/futumoo/agents", response_model=AgentCreatedResponse)
        def create_futumoo_agent(request: CreateAgentRequest) -> AgentCreatedResponse:
            return _create_agent_handler(
                request, lambda: get_state().futumoo_arena, ("HKD", "USD")
            )

    if ashare_enabled:
        @api.api_route("/A-share/mcp", methods=["GET", "POST", "DELETE"])
        def mcp_redirect() -> RedirectResponse:
            target = f"{base_url}/A-share/mcp/" if base_url else "/A-share/mcp/"
            return RedirectResponse(url=target, status_code=307)

    if futumoo_enabled:
        @api.api_route("/futumoo/mcp", methods=["GET", "POST", "DELETE"])
        def futumoo_mcp_redirect() -> RedirectResponse:
            target = f"{base_url}/futumoo/mcp/" if base_url else "/futumoo/mcp/"
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
