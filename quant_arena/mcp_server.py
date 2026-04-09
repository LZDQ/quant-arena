"""Official MCP server integration for quant-arena."""

import json
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from quant_arena.schemas import SubmitOrderRequest
from quant_arena.arena import ArenaService
from quant_arena.errors import ServiceError


_CURRENT_AGENT_ID: ContextVar[str | None] = ContextVar("quant_arena_current_agent_id", default=None)


def _get_current_agent_id() -> str:
    agent_id = _CURRENT_AGENT_ID.get()
    if not agent_id:
        raise RuntimeError("No authenticated agent in MCP request context")
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

    @mcp.resource("arena://portfolio")
    def portfolio_resource() -> str:
        portfolio = get_arena().get_portfolio(_get_current_agent_id()).model_dump(mode="json")
        return json.dumps(portfolio, ensure_ascii=False)

    @mcp.resource("arena://operations")
    def operations_resource() -> str:
        operations = get_arena().list_operations(_get_current_agent_id(), limit=50).model_dump(mode="json")
        return json.dumps(operations, ensure_ascii=False)

    @mcp.tool()
    def get_portfolio() -> dict[str, Any]:
        """Get current portfolio including pending orders."""

        return get_arena().get_portfolio(_get_current_agent_id()).model_dump(mode="json")

    @mcp.tool()
    def list_operations(
        limit: int | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        """List orders and fills for the authenticated agent."""

        parsed_start = datetime.fromisoformat(start) if start else None
        parsed_end = datetime.fromisoformat(end) if end else None
        return get_arena().list_operations(
            _get_current_agent_id(),
            start=parsed_start,
            end=parsed_end,
            limit=limit,
        ).model_dump(mode="json")

    @mcp.tool()
    def submit_operation(code: str, side: str, quantity: int, limit_price: float) -> dict[str, Any]:
        """Submit a pending buy or sell limit order."""

        order = get_arena().submit_order(
            _get_current_agent_id(),
            SubmitOrderRequest(
                code=code,
                side=side,
                quantity=quantity,
                limit_price=limit_price,
            ),
        )
        return order.model_dump(mode="json")

    @mcp.tool()
    def cancel_operation(order_id: str) -> dict[str, Any]:
        """Cancel a pending order."""

        order = get_arena().cancel_order(_get_current_agent_id(), order_id)
        return order.model_dump(mode="json")

    return mcp


def wrap_mcp_with_agent_auth(mcp_app: ASGIApp, get_arena: Callable[[], ArenaService]) -> ASGIApp:
    """Guard the mounted MCP app with agent token auth."""

    async def authenticated_app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await mcp_app(scope, receive, send)
            return

        raw_headers = list(scope.get("headers", []))
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in raw_headers
        }
        try:
            agent_id = get_arena().authenticate_agent(headers)
        except ServiceError as exc:
            response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
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
