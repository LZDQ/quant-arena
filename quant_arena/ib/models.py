"""Persisted state shapes for the IB arena.

The IB Gateway is the source of truth for cash, positions, and orders,
so the local state stays minimal — only the equity history is persisted
locally so the dashboard can show a return-% trend across days. The
`orders`, `fills`, and `positions` fields are kept (empty) for base
arena compatibility but never populated.
"""

from pydantic import BaseModel, Field

from quant_arena.models import EquityPoint, FillRecord, OrderRecord


class IBAgentState(BaseModel):
    """Persisted runtime state for one IB agent.

    Cash and positions live on the IB Gateway, not here. We only
    persist the equity history — daily NetLiquidation snapshots —
    so the dashboard can render a curve.
    """

    agent_id: str
    cash: float = Field(
        default=0.0,
        description="Unused — IB Gateway is the source of truth for cash. Kept for base-arena compatibility.",
    )
    realized_pnl: float = 0.0
    orders: list[OrderRecord] = Field(default_factory=list)
    fills: list[FillRecord] = Field(default_factory=list)
    positions: dict[str, int] = Field(default_factory=dict)
    equity_history: list[EquityPoint] = Field(
        default_factory=list,
        description="Daily NetLiquidation snapshots in the agent's base currency.",
    )
