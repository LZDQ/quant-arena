"""Persisted state shapes for the EODHD paper-trading arena."""

from pydantic import BaseModel, Field

from quant_arena.models import EquityPoint, FillRecord, ManualPositionClearRecord, OrderRecord


class EODHDPosition(BaseModel):
    """One open long position.

    EODHD is used here as a data source instead of a broker-region rule engine,
    so a single quantity / average-cost pair is enough.
    """

    quantity: int = Field(ge=0)
    avg_cost: float = Field(ge=0)


class EODHDAgentState(BaseModel):
    """Persisted runtime state for one EODHD paper-trading agent.

    The agent's currency is a property of `AgentConfig`; every monetary field
    below is in that single configured currency.
    """

    agent_id: str
    cash: float
    realized_pnl: float = 0.0
    orders: list[OrderRecord] = Field(default_factory=list)
    fills: list[FillRecord] = Field(default_factory=list)
    positions: dict[str, EODHDPosition] = Field(default_factory=dict)
    equity_history: list[EquityPoint] = Field(
        default_factory=list,
        description="Daily equity history plus the current in-memory point rendered by the dashboard.",
    )
    manual_position_clears: list[ManualPositionClearRecord] = Field(
        default_factory=list,
        description="历次手动清仓重置事件",
    )
