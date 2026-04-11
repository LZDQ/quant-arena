"""Official MCP server integration for quant-arena."""

from contextvars import ContextVar
from datetime import datetime
from typing import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from quant_arena.arena import ArenaService
from quant_arena.errors import BadRequestError
from quant_arena.models import AgentMetadata, MonitoredAgentSnapshot, OperationLog, OrderRecord, PortfolioSnapshot, SubmitOrder


_CURRENT_AGENT_ID: ContextVar[str | None] = ContextVar("quant_arena_current_agent_id", default=None)


def _get_current_agent_id() -> str:
    agent_id = _CURRENT_AGENT_ID.get()
    if not agent_id:
        raise RuntimeError("No authenticated agent in MCP request context")
    return agent_id


def _require_monitor_agent(get_arena: Callable[[], ArenaService]) -> str:
    agent_id = _get_current_agent_id()
    agent = get_arena().get_agent(agent_id)
    if agent.role != "monitor":
        raise BadRequestError("This MCP tool is only available to monitor agents")
    return agent_id


def create_mcp_server(get_arena: Callable[[], ArenaService]) -> FastMCP:
    """Create the official MCP server."""

    mcp = FastMCP(
        "quant-arena",
        json_response=True,
        stateless_http=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        ),
    )

    @mcp.resource("arena://market-data-path")
    def market_data_path() -> str:
        return str(get_arena().market.market_data_root)

    @mcp.tool()
    def get_portfolio() -> PortfolioSnapshot:
        """Get current portfolio including pending orders."""

        return get_arena().get_portfolio(_get_current_agent_id())

    @mcp.tool()
    def list_operations(
        agent_id: str | None = None,
        limit: int | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> OperationLog:
        """List orders and fills. Normal agents can only inspect themselves."""

        current_agent_id = _get_current_agent_id()
        target_agent_id = agent_id or current_agent_id
        if target_agent_id != current_agent_id:
            _require_monitor_agent(get_arena)
        parsed_start = datetime.fromisoformat(start) if start else None
        parsed_end = datetime.fromisoformat(end) if end else None
        return get_arena().list_operations(
            target_agent_id,
            start=parsed_start,
            end=parsed_end,
            limit=limit,
        )

    @mcp.tool()
    def get_self_metadata() -> AgentMetadata:
        """Get the authenticated agent's own metadata."""

        agent_id = _get_current_agent_id()
        agent = get_arena().get_agent(agent_id)
        return AgentMetadata(
            agent_id=agent_id,
            name=agent_id,
            display_name=agent.display_name,
            role=agent.role,
        )

    @mcp.tool()
    def submit_operation(code: str, side: str, quantity: int, limit_price: float, comment: str) -> OrderRecord:
        """Submit a pending buy or sell limit order."""

        order = get_arena().submit_order(
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
        """Cancel a pending order."""

        order = get_arena().cancel_order(_get_current_agent_id(), order_id)
        return order

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


def wrap_mcp_with_agent_auth(
    mcp_app: ASGIApp,
    get_arena: Callable[[], ArenaService],
) -> ASGIApp:
    """Guard the mounted MCP app with Bearer token auth."""

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
            response = JSONResponse(status_code=401, content={"detail": "Invalid agent token"})
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
