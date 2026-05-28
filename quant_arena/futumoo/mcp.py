"""Futumoo MCP server: agent-token auth on top of the HK/US paper-trading arena."""

from contextvars import ContextVar
from datetime import timezone
from typing import Callable

from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp

from quant_arena.futumoo.arena import FutumooArenaService
from quant_arena.mcp_auth import make_agent_auth_wrapper
from quant_arena.mcp_common import make_arena_mcp_server


_CURRENT_AGENT_ID: ContextVar[str | None] = ContextVar(
    "quant_arena_futumoo_current_agent_id", default=None
)


def create_futumoo_mcp_server(
    get_arena: Callable[[], FutumooArenaService],
) -> FastMCP:
    """Create the Futumoo HK/US paper-trading MCP server."""

    return make_arena_mcp_server(
        name="quant-arena-futumoo",
        get_arena=get_arena,
        current_agent_id=_CURRENT_AGENT_ID,
        submit_operation_description=(
            "Submit a limit-price buy or sell order on the Futumoo paper arena. "
            "Each agent trades in a single currency (HKD or USD); HKD agents "
            "may only submit `HK.<code>` symbols and USD agents may only submit "
            "`US.<ticker>` symbols. Orders are queued as pending and matched "
            "against `last_price` polled from Futu OpenD. Submissions are "
            "validated against the region's session window, trading-day "
            "calendar, suspension flag, and side-specific rules: HK buys must "
            "be a multiple of the per-symbol board lot, sells require "
            "sufficient sellable inventory, and the US side enforces the FINRA "
            "pattern-day-trader limit (max 3 day-trades in any rolling 5 US "
            "business days while total equity is below 25,000 USD). Invalid "
            "orders are rejected at submission and never appear in the order log."
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
