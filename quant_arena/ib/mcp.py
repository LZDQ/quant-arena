"""IB MCP server: per-agent token auth, paper/real dispatch.

The bearer token presented at request time identifies the calling
IB agent. The agent's `ib_mode` (paper or real) selects which
`IBService` handles the call. The two modes share the same MCP
endpoint URL — the token is the only thing that distinguishes them.

Concurrency model: only one MCP client is allowed per mode at a time.
Each mode owns a `threading.Lock` that wraps the entire MCP request,
so a second concurrent client for the same mode is rejected with 409.
"""

import threading
from contextvars import ContextVar
from logging import getLogger
from typing import Callable, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from quant_arena.ib.arena import IBArenaService
from quant_arena.ib.service import (
    IBAccountValueInfo,
    IBFillInfo,
    IBOrderType,
    IBPositionInfo,
    IBService,
    IBSide,
    IBSubmitOrderRequest,
    IBTradeInfo,
)

logger = getLogger(__name__)


_CURRENT_IB_SERVICE: ContextVar[IBService | None] = ContextVar(
    "quant_arena_current_ib_service", default=None
)


def _current_service() -> IBService:
    service = _CURRENT_IB_SERVICE.get()
    if service is None:
        raise RuntimeError("No authenticated IB service in MCP request context")
    return service


def create_ib_mcp_server() -> FastMCP:
    """Create the IB paper/real MCP server.

    The server is mode-agnostic; the active IBService is bound via the
    `_CURRENT_IB_SERVICE` ContextVar by the auth wrapper before each
    tool invocation.
    """

    mcp = FastMCP(
        "quant-arena-ib",
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
    def get_mode() -> Literal["paper", "real"]:
        """Return whether this connection is bound to paper or real."""

        return _current_service().mode

    @mcp.tool()
    def get_account_summary() -> list[IBAccountValueInfo]:
        """Return the IB account summary tags (NetLiquidation, AvailableFunds, etc.)."""

        return _current_service().get_account_summary()

    @mcp.tool()
    def get_positions() -> list[IBPositionInfo]:
        """Return current positions held in the IB account."""

        return _current_service().get_positions()

    @mcp.tool()
    def get_open_trades() -> list[IBTradeInfo]:
        """Return all open IB orders/trades."""

        return _current_service().get_open_trades()

    @mcp.tool()
    def get_recent_fills() -> list[IBFillInfo]:
        """Return recent IB executions/fills (today's session)."""

        return _current_service().get_recent_fills()

    @mcp.tool()
    def submit_order(
        symbol: str,
        side: IBSide,
        quantity: float,
        order_type: IBOrderType = "LMT",
        limit_price: float | None = None,
        exchange: str | None = None,
        currency: str | None = None,
        tif: str = "DAY",
    ) -> IBTradeInfo:
        """Submit a buy or sell order on the IB account.

        order_type=LMT requires limit_price; order_type=MKT must omit it.
        HK and US trades are distinguished by `exchange` and `currency`
        — e.g. `exchange="SMART", currency="HKD"` for HKEX listings,
        `exchange="SMART", currency="USD"` for US listings. When omitted
        the server's default exchange/currency are used.
        """

        request = IBSubmitOrderRequest(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            exchange=exchange,
            currency=currency,
            tif=tif,
        )
        return _current_service().submit_order(request)

    @mcp.tool()
    def cancel_order(order_id: int) -> IBTradeInfo:
        """Cancel an open IB order by its IB orderId."""

        return _current_service().cancel_order(order_id)

    return mcp


def wrap_ib_mcp_with_agent_auth(
    mcp_app: ASGIApp,
    get_arena: Callable[[], IBArenaService],
    get_paper: Callable[[], IBService],
    get_real: Callable[[], IBService],
) -> ASGIApp:
    """Bearer-token wrapper.

    The token must match an enabled IB agent's `token_secret`. The
    matching agent's `ib_mode` selects whether the call is dispatched
    to the paper or real `IBService`. At most one in-flight client is
    allowed per mode.
    """

    paper_lock = threading.Lock()
    real_lock = threading.Lock()

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
        token_value: str | None = None
        if authorization.startswith("Bearer "):
            token_value = authorization[len("Bearer "):]

        mode: Literal["paper", "real"] | None = None
        service: IBService | None = None
        lock: threading.Lock | None = None
        if token_value:
            arena = get_arena()
            for _, agent in arena.list_agents():
                if not agent.enabled or agent.token_secret != token_value:
                    continue
                if agent.ib_mode == "paper":
                    mode = "paper"
                    service = get_paper()
                    lock = paper_lock
                elif agent.ib_mode == "real":
                    mode = "real"
                    service = get_real()
                    lock = real_lock
                break

        if mode is None or service is None or lock is None:
            response = JSONResponse(
                status_code=401,
                content={"detail": "Invalid IB agent token"},
            )
            await response(scope, receive, send)
            return

        if not lock.acquire(blocking=False):
            logger.warning(
                "Rejecting IB %s MCP request: another client is already in flight",
                mode,
            )
            response = JSONResponse(
                status_code=409,
                content={
                    "detail": (
                        f"Only one IB MCP client is allowed for {mode}; "
                        "another client is currently in flight"
                    )
                },
            )
            await response(scope, receive, send)
            return

        accept = headers.get("accept")
        if accept is None or "application/json" not in accept:
            raw_headers = [(key, value) for key, value in raw_headers if key.lower() != b"accept"]
            raw_headers.append((b"accept", b"application/json"))
            scope = dict(scope)
            scope["headers"] = raw_headers

        token = _CURRENT_IB_SERVICE.set(service)
        try:
            await mcp_app(scope, receive, send)
        finally:
            _CURRENT_IB_SERVICE.reset(token)
            lock.release()

    return authenticated_app
