"""Persisted state shapes for the Futumoo offline paper-trading branch."""

from pydantic import BaseModel, Field

from quant_arena.models import EquityPoint, FillRecord, OrderRecord


class FutumooPosition(BaseModel):
    """One open position. No T+1 lots — markets covered span jurisdictions."""

    quantity: int = Field(ge=0)
    avg_cost: float = Field(ge=0)


class FutumooAgentState(BaseModel):
    """Persisted runtime state for one Futumoo paper-trading agent."""

    agent_id: str
    cash: float
    realized_pnl: float = 0.0
    orders: list[OrderRecord] = Field(default_factory=list)
    fills: list[FillRecord] = Field(default_factory=list)
    positions: dict[str, FutumooPosition] = Field(default_factory=dict)
    equity_history: list[EquityPoint] = Field(default_factory=list)
