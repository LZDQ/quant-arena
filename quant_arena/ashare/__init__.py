"""A-share specific services: market data, arena simulator, MCP server."""

from quant_arena.ashare.arena import ArenaService
from quant_arena.ashare.mcp import create_ashare_mcp_server, wrap_mcp_with_agent_auth
from quant_arena.ashare.service import AShareService

__all__ = [
    "AShareService",
    "ArenaService",
    "create_ashare_mcp_server",
    "wrap_mcp_with_agent_auth",
]
