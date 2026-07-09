"""Futumoo HK + US + CN paper-trading branch.

Trading is fully offline — orders are queued as pending and matched
against `last_price` snapshots polled from Futu OpenD. Each agent
chooses one currency (HKD, USD, or CNY), and the HK, US, and CN books are governed by
separate `RegionArena` strategies
that own their session windows, trading-day calendars, lot-size
rules, and (for the US) Pattern-Day-Trader enforcement. Symbols
must use the `HK.`, `US.`, `SH.`, or `SZ.` Futu prefix; other markets are rejected.
"""

from quant_arena.futumoo.arena import FutumooArenaService
from quant_arena.futumoo.mcp import (
    create_futumoo_mcp_server,
    wrap_futumoo_mcp_with_agent_auth,
)
from quant_arena.futumoo.region import CNRegionArena, HKRegionArena, RegionArena, USRegionArena
from quant_arena.futumoo.service import FutumooService

__all__ = [
    "FutumooArenaService",
    "FutumooService",
    "CNRegionArena",
    "HKRegionArena",
    "RegionArena",
    "USRegionArena",
    "create_futumoo_mcp_server",
    "wrap_futumoo_mcp_with_agent_auth",
]
