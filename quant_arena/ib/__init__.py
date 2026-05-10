"""Interactive Brokers paper / real trading branch.

Connects to an external IB Gateway (or TWS) via `ib_insync`. Each
`IBArenaService` wraps two `IBService` connections — one for the
paper account, one for the real account — and at most one agent
may be registered against each. The IB MCP server uses the agent's
token_secret to dispatch tool calls to the right connection.

HK and US trading is distinguished at order time via the IB contract's
`exchange` and `currency` fields, not by the agent. A single IB
account can hold both HK and US positions.
"""

from quant_arena.ib.arena import IBArenaService
from quant_arena.ib.mcp import create_ib_mcp_server, wrap_ib_mcp_with_agent_auth
from quant_arena.ib.service import IBService

__all__ = [
    "IBArenaService",
    "IBService",
    "create_ib_mcp_server",
    "wrap_ib_mcp_with_agent_auth",
]
