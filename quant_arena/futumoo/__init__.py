"""Futumoo offline paper-trading branch.

Trading is fully offline — orders fill instantly at the user's limit
price in our own ledger. The Futu OpenD `OpenQuoteContext` is used only
to fetch current snapshot prices once per day for equity-history
mark-to-market. Symbols carry their Futu region prefix verbatim
(`US.AAPL`, `HK.00700`, `SH.600519`, …) and are not split per market.
"""

from quant_arena.futumoo.arena import FutumooArenaService
from quant_arena.futumoo.mcp import create_futumoo_mcp_server, wrap_futumoo_mcp_with_agent_auth
from quant_arena.futumoo.service import FutumooService

__all__ = [
    "FutumooArenaService",
    "FutumooService",
    "create_futumoo_mcp_server",
    "wrap_futumoo_mcp_with_agent_auth",
]
