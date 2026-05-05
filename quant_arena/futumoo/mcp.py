"""Futumoo MCP server: agent-token auth on top of the offline simulator."""

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from quant_arena.errors import BadRequestError
from quant_arena.futumoo.arena import FutumooArenaService
from quant_arena.models import (
    AgentMetadata,
    DailyReport,
    MonitoredAgentSnapshot,
    OperationLog,
    OrderRecord,
    PortfolioSnapshot,
    SubmitOrder,
)


_CURRENT_AGENT_ID: ContextVar[str | None] = ContextVar(
    "quant_arena_futumoo_current_agent_id", default=None
)


def _get_current_agent_id() -> str:
    agent_id = _CURRENT_AGENT_ID.get()
    if not agent_id:
        raise RuntimeError("No authenticated agent in Futumoo MCP request context")
    return agent_id


def _parse_filter_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _require_monitor_agent(get_arena: Callable[[], FutumooArenaService]) -> str:
    agent_id = _get_current_agent_id()
    agent = get_arena().get_agent(agent_id)
    if agent.role != "monitor":
        raise BadRequestError("This MCP tool is only available to monitor agents")
    return agent_id


def create_futumoo_mcp_server(
    get_arena: Callable[[], FutumooArenaService],
) -> FastMCP:
    """Create the Futumoo offline-paper MCP server."""

    mcp = FastMCP(
        "quant-arena-futumoo",
        json_response=True,
        stateless_http=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        ),
    )

    @mcp.tool()
    def get_portfolio() -> PortfolioSnapshot:
        """Get the calling agent's current portfolio."""

        return get_arena().get_portfolio(_get_current_agent_id())

    @mcp.tool()
    def list_operations(
        agent_id: str | None = None,
        limit: int = 10,
        start: str | None = None,
        end: str | None = None,
    ) -> OperationLog:
        """List orders and fills. Normal agents can only inspect themselves.

        `start` and `end` are optional ISO 8601 datetime filters. `limit` defaults
        to the last 10 matching orders and fills.
        """

        current_agent_id = _get_current_agent_id()
        target_agent_id = agent_id or current_agent_id
        if target_agent_id != current_agent_id:
            _require_monitor_agent(get_arena)
        parsed_start = _parse_filter_datetime(start)
        parsed_end = _parse_filter_datetime(end)
        return get_arena().list_operations(
            target_agent_id,
            start=parsed_start,
            end=parsed_end,
            limit=limit,
        )

    @mcp.tool()
    def get_self_metadata() -> AgentMetadata:
        """Get the current agent's metadata."""

        agent_id = _get_current_agent_id()
        agent = get_arena().get_agent(agent_id)
        return AgentMetadata(
            agent_id=agent_id,
            name=agent_id,
            display_name=agent.display_name,
            role=agent.role,
        )

    @mcp.tool()
    async def submit_operation(
        code: str, side: str, quantity: int, limit_price: float, comment: str
    ) -> OrderRecord:
        """Submit a buy or sell order. The order fills instantly at `limit_price`.

        `code` is the Futu-namespaced symbol, e.g. `US.AAPL`, `HK.00700`,
        `SH.600519`. There are no per-market lot-size, T+1, or price-band
        restrictions — the simulator is fully offline.
        """

        order = await get_arena().submit_order(
            _get_current_agent_id(),
            SubmitOrder(
                code=code,
                side=side,
                quantity=quantity,
                limit_price=limit_price,
                comment=comment,
            ),
        )
        return order

    @mcp.tool()
    def cancel_operation(order_id: str) -> OrderRecord:
        """Cancel a still-pending order. Filled orders cannot be canceled."""

        return get_arena().cancel_order(_get_current_agent_id(), order_id)

    @mcp.tool()
    def submit_daily_report(content: str) -> str:
        """Create or overwrite today's daily report (markdown) for the calling agent."""

        report = get_arena().submit_daily_report(_get_current_agent_id(), content)
        line_count = len(report.content.splitlines())
        char_count = len(report.content)
        return (
            f"Saved daily report for {report.trade_date.isoformat()}: "
            f"{line_count} lines, {char_count} characters."
        )

    @mcp.tool()
    def get_last_daily_report_before_today() -> DailyReport | str:
        """Return the calling agent's most recent daily report whose date is strictly before today."""

        report = get_arena().get_last_daily_report_before_today(_get_current_agent_id())
        if report is None:
            return "No previous daily report found."
        return report

    @mcp.tool()
    def get_agent_last_daily_report(agent_id: str) -> DailyReport | str:
        """Return the latest daily report for the given agent. Monitor agents only."""

        _require_monitor_agent(get_arena)
        report = get_arena().get_latest_daily_report(agent_id)
        if report is None:
            return f"No daily report found for agent {agent_id}."
        return report

    @mcp.tool()
    def get_current_rankings() -> list[MonitoredAgentSnapshot]:
        """Get current rankings with portfolio snapshots. Monitor agents only."""

        _require_monitor_agent(get_arena)
        arena = get_arena()
        entries: list[MonitoredAgentSnapshot] = []
        for ranking in arena.get_rankings():
            agent = arena.get_agent(ranking.agent_id)
            entries.append(
                MonitoredAgentSnapshot(
                    agent_id=ranking.agent_id,
                    name=ranking.agent_id,
                    display_name=agent.display_name,
                    role=agent.role,
                    portfolio=arena.get_portfolio(ranking.agent_id),
                )
            )
        return entries

    return mcp


def wrap_futumoo_mcp_with_agent_auth(
    mcp_app: ASGIApp,
    get_arena: Callable[[], FutumooArenaService],
) -> ASGIApp:
    """Bearer-token wrapper that resolves the calling agent for the Futumoo MCP."""

    async def authenticated_app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await mcp_app(scope, receive, send)
            return

        raw_headers = list(scope.get("headers", []))
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in raw_headers
        }
        authorization = headers.get("authorization", "")
        token_value = None
        if authorization.startswith("Bearer "):
            token_value = authorization[len("Bearer "):]
        agent_id = None
        if token_value is not None:
            for candidate_id, agent in get_arena().list_agents():
                if agent.enabled and agent.token_secret == token_value:
                    agent_id = candidate_id
                    break
        if agent_id is None:
            response = JSONResponse(status_code=401, content={"detail": "Invalid futumoo agent token"})
            await response(scope, receive, send)
            return

        accept = headers.get("accept")
        if accept is None or "application/json" not in accept:
            raw_headers = [(key, value) for key, value in raw_headers if key.lower() != b"accept"]
            raw_headers.append((b"accept", b"application/json"))
            scope = dict(scope)
            scope["headers"] = raw_headers

        token = _CURRENT_AGENT_ID.set(agent_id)
        try:
            await mcp_app(scope, receive, send)
        finally:
            _CURRENT_AGENT_ID.reset(token)

    return authenticated_app
