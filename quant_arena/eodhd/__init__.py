"""EODHD paper-trading and market-data arena.

EODHD runs as its own arena. It uses the `eodhd` Python package for live quotes,
bulk daily bars, intraday bars, and symbol lists, and stores its CSV cache under
the configured EODHD market-data root.
"""

from quant_arena.eodhd.arena import EODHDArenaService
from quant_arena.eodhd.mcp import (
    create_eodhd_mcp_server,
    wrap_eodhd_mcp_with_agent_auth,
)
from quant_arena.eodhd.service import EODHDService

__all__ = [
    "EODHDArenaService",
    "EODHDService",
    "create_eodhd_mcp_server",
    "wrap_eodhd_mcp_with_agent_auth",
]
