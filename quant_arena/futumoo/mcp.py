"""Futumoo MCP server: agent-token auth on top of the offline simulator."""

from contextvars import ContextVar
from datetime import timezone
from typing import Callable

from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp

from quant_arena.futumoo.arena import FutumooArenaService
from quant_arena.mcp_common import make_agent_auth_wrapper, make_arena_mcp_server


_CURRENT_AGENT_ID: ContextVar[str | None] = ContextVar(
    "quant_arena_futumoo_current_agent_id", default=None
)


def create_futumoo_mcp_server(
    get_arena: Callable[[], FutumooArenaService],
) -> FastMCP:
    """Create the Futumoo offline-paper MCP server."""

    return make_arena_mcp_server(
        name="quant-arena-futumoo",
        get_arena=get_arena,
        current_agent_id=_CURRENT_AGENT_ID,
        submit_operation_description=(
            "Submit a buy or sell order on the Futumoo offline simulator. "
            "The order fills instantly at `limit_price`. `code` is the "
            "Futu-namespaced symbol, e.g. `US.AAPL`, `HK.00700`, `SH.600519`. "
            "There are no per-market lot-size, T+1, or price-band restrictions."
        ),
        fallback_tz=timezone.utc,
    )


def wrap_futumoo_mcp_with_agent_auth(
    mcp_app: ASGIApp,
    get_arena: Callable[[], FutumooArenaService],
) -> ASGIApp:
    """Bearer-token wrapper that resolves the calling agent for the Futumoo MCP."""

    return make_agent_auth_wrapper(
        mcp_app,
        get_arena,
        _CURRENT_AGENT_ID,
        invalid_token_detail="Invalid futumoo agent token",
    )
