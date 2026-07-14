"""Futumoo MCP server: agent-token auth on top of the HK/US/CN paper arena."""

from contextvars import ContextVar
from datetime import date, datetime, timezone
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
    SpecialEvent,
    SubmitOrder,
)


_CURRENT_AGENT_ID: ContextVar[str | None] = ContextVar(
    "quant_arena_futumoo_current_agent_id", default=None
)
_AGENT_TOKEN_HEADER = "quant-arena-token"


def _request_headers(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _extract_agent_token(headers: dict[str, str]) -> str | None:
    token = headers.get(_AGENT_TOKEN_HEADER) or None
    if token is None:
        authorization = headers.get("authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization[len("Bearer "):]
    return token


def _ensure_json_accept(scope: Scope, headers: dict[str, str]) -> Scope:
    accept = headers.get("accept")
    if accept is not None and "application/json" in accept:
        return scope
    raw_headers = [
        (key, value)
        for key, value in scope.get("headers", [])
        if key.lower() != b"accept"
    ]
    raw_headers.append((b"accept", b"application/json"))
    next_scope = dict(scope)
    next_scope["headers"] = raw_headers
    return next_scope


def _current_agent_id() -> str:
    agent_id = _CURRENT_AGENT_ID.get()
    if not agent_id:
        raise RuntimeError("No authenticated futumoo agent in MCP request context")
    return agent_id


def create_futumoo_mcp_server(
    get_arena: Callable[[], FutumooArenaService],
) -> FastMCP:
    """Create the Futumoo HK/US/CN paper-trading MCP server."""

    def parse_filter_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment

    def require_monitor_agent() -> str:
        agent_id = _current_agent_id()
        agent = get_arena().get_agent(agent_id)
        if agent.role != "monitor":
            raise BadRequestError("This tool is only available for monitor agents.")
        return agent_id

    mcp = FastMCP(
        "quant-arena-futumoo",
        json_response=True,
        stateless_http=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        ),
    )

    @mcp.tool()
    def get_portfolio() -> PortfolioSnapshot:
        """Get current portfolio including pending orders."""

        return get_arena().get_portfolio(_current_agent_id())

    @mcp.tool()
    def list_operations(
        agent_id: str | None = None,
        limit: int = 10,
        start: str | None = None,
        end: str | None = None,
    ) -> OperationLog:
        """List orders and fills."""

        current = _current_agent_id()
        agent = get_arena().get_agent(current)
        target = current if agent.role == "normal" else agent_id or current
        return get_arena().list_operations(
            target,
            start=parse_filter_datetime(start),
            end=parse_filter_datetime(end),
            limit=limit,
        )

    @mcp.tool()
    def list_special_events(
        agent_id: str | None = None,
        limit: int = 20,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[SpecialEvent]:
        """List special account events such as manual position clears."""

        current = _current_agent_id()
        target = agent_id or current
        if target != current:
            require_monitor_agent()
        return get_arena().list_special_events(
            target,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
            limit=limit,
        )

    @mcp.tool()
    def get_self_metadata() -> AgentMetadata:
        """Get the current agent's metadata."""

        agent_id = _current_agent_id()
        agent = get_arena().get_agent(agent_id)
        return AgentMetadata(
            agent_id=agent_id,
            name=agent_id,
            display_name=agent.display_name,
            role=agent.role,
            currency=agent.currency,
        )

    @mcp.tool(
        description=(
            "Submit a limit-price buy or sell order on the Futumoo paper arena. "
            "Each agent trades in a single currency (HKD, USD, or CNY); HKD agents "
            "may only submit `HK.<code>` symbols and USD agents may only submit "
            "`US.<ticker>` symbols; CNY agents may only submit `SH.<code>` or "
            "`SZ.<code>` symbols. Orders are queued as pending and matched "
            "against event-driven real-time `last_price` pushes from Futu "
            "OpenD. Submissions are "
            "validated against the region's session window, trading-day "
            "calendar, suspension flag, and side-specific rules: HK/CN buys "
            "must be a multiple of the per-symbol lot size, sells require "
            "sufficient inventory, and the US side enforces the FINRA "
            "pattern-day-trader limit (max 3 day-trades in any rolling 5 US "
            "business days while total equity is below 25,000 USD). Invalid "
            "orders are rejected at submission and never appear in the order log."
        )
    )
    async def submit_operation(
        code: str, side: str, quantity: int, limit_price: float, comment: str
    ) -> OrderRecord:
        return await get_arena().submit_order(
            _current_agent_id(),
            SubmitOrder(
                code=code,
                side=side,
                quantity=quantity,
                limit_price=limit_price,
                comment=comment,
            ),
        )

    @mcp.tool()
    def cancel_operation(order_id: str) -> OrderRecord:
        """Cancel a pending order."""

        return get_arena().cancel_order(_current_agent_id(), order_id)

    @mcp.tool()
    def submit_daily_report(content: str) -> str:
        """Create or overwrite today's markdown daily report."""

        report = get_arena().submit_daily_report(_current_agent_id(), content)
        line_count = len(report.content.splitlines())
        char_count = len(report.content)
        return (
            f"Saved daily report for {report.trade_date.isoformat()}: "
            f"{line_count} lines, {char_count} characters."
        )

    @mcp.tool()
    def get_last_daily_report_before_today() -> DailyReport | str:
        """Return the caller's latest report whose date is before today."""

        report = get_arena().get_last_daily_report_before_today(_current_agent_id())
        if report is None:
            return "No previous daily report found."
        return report

    @mcp.tool()
    def get_agent_last_daily_report(agent_id: str) -> DailyReport | str:
        """Return the latest daily report for another agent. Monitor agents only."""

        require_monitor_agent()
        report = get_arena().get_latest_daily_report(agent_id)
        if report is None:
            return f"No daily report found for agent {agent_id}."
        return report

    @mcp.tool()
    def get_current_rankings() -> list[MonitoredAgentSnapshot]:
        """Get current rankings with portfolio snapshots. Monitor agents only."""

        require_monitor_agent()
        arena = get_arena()
        snapshots: list[MonitoredAgentSnapshot] = []
        for ranking in arena.get_rankings():
            agent = arena.get_agent(ranking.agent_id)
            snapshots.append(
                MonitoredAgentSnapshot(
                    agent_id=ranking.agent_id,
                    name=ranking.agent_id,
                    display_name=agent.display_name,
                    role=agent.role,
                    currency=agent.currency,
                    initial_cash=agent.initial_cash,
                    return_pct=ranking.return_pct,
                    portfolio=arena.get_portfolio(ranking.agent_id),
                )
            )
        return snapshots

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

        headers = _request_headers(scope)
        token_value = _extract_agent_token(headers)
        agent_id = None
        if token_value is not None:
            for candidate_id, agent in get_arena().list_agents():
                if agent.enabled and agent.token_secret == token_value:
                    agent_id = candidate_id
                    break
        if agent_id is None:
            response = JSONResponse(
                status_code=401,
                content={"detail": "Invalid futumoo agent token"},
            )
            await response(scope, receive, send)
            return

        next_scope = _ensure_json_accept(scope, headers)
        token = _CURRENT_AGENT_ID.set(agent_id)
        try:
            await mcp_app(next_scope, receive, send)
        finally:
            _CURRENT_AGENT_ID.reset(token)

    return authenticated_app
