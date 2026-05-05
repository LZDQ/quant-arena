"""A-share MCP server integration for quant-arena."""

from contextvars import ContextVar
from typing import Callable

from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp

from quant_arena.ashare.arena import ArenaService
from quant_arena.clock import SHANGHAI_TZ
from quant_arena.mcp_common import make_agent_auth_wrapper, make_arena_mcp_server


_CURRENT_AGENT_ID: ContextVar[str | None] = ContextVar(
    "quant_arena_current_agent_id", default=None
)


def create_ashare_mcp_server(get_arena: Callable[[], ArenaService]) -> FastMCP:
    """Create the A-share arena MCP server."""

    mcp = make_arena_mcp_server(
        name="quant-arena-ashare",
        get_arena=get_arena,
        current_agent_id=_CURRENT_AGENT_ID,
        submit_operation_description=(
            "Submit a pending buy or sell limit order on the A-share simulator. "
            "Settlement is T+1; buy quantity must be a multiple of 100; only "
            "main-board codes (SH 60xxxx, SZ 000/001/002/003 xxxx) are accepted."
        ),
        fallback_tz=SHANGHAI_TZ,
    )

    @mcp.resource("arena://market-data-path")
    def market_data_path() -> str:
        return str(get_arena().market.market_data_root)

    return mcp


def wrap_mcp_with_agent_auth(
    mcp_app: ASGIApp,
    get_arena: Callable[[], ArenaService],
) -> ASGIApp:
    """Guard the mounted A-share MCP app with bearer-token auth."""

    return make_agent_auth_wrapper(
        mcp_app,
        get_arena,
        _CURRENT_AGENT_ID,
        invalid_token_detail="Invalid agent token",
    )
