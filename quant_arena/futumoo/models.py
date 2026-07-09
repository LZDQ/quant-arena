"""Persisted state shapes for the Futumoo (HK or US) paper-trading arena."""

from datetime import date

from pydantic import BaseModel, Field

from quant_arena.models import EquityPoint, FillRecord, ManualPositionClearRecord, OrderRecord


class FutumooPosition(BaseModel):
    """One open long position. HK and US neither enforce T+1 sellability,
    so a single (qty, avg_cost) pair is sufficient — no per-lot tracking."""

    quantity: int = Field(ge=0)
    avg_cost: float = Field(ge=0)


class DayTradeRecord(BaseModel):
    """One US day-trade event used by the pattern-day-trader counter.

    Recorded the first time a (US date, code) pair sees both a buy and a
    sell fill. Subsequent same-day round-trips on the same code do not add
    additional records — the deliberately lenient simplification documented
    on `USRegionArena`. Only meaningful for `currency == "USD"` agents.
    """

    trade_date: date
    code: str


class FutumooAgentState(BaseModel):
    """Persisted runtime state for one Futumoo paper-trading agent.

    The agent's currency is a property of `AgentConfig`, not of the state
    itself; every monetary field below is in that single currency.
    """

    agent_id: str
    cash: float
    realized_pnl: float = 0.0
    orders: list[OrderRecord] = Field(default_factory=list)
    fills: list[FillRecord] = Field(default_factory=list)
    positions: dict[str, FutumooPosition] = Field(default_factory=dict)
    day_trades: list[DayTradeRecord] = Field(default_factory=list)
    equity_history: list[EquityPoint] = Field(
        default_factory=list,
        description="Reserved for future persisted daily history; currently the Futumoo arena refreshes only today's in-memory equity point.",
    )
    manual_position_clears: list[ManualPositionClearRecord] = Field(
        default_factory=list,
        description="历次手动清仓重置事件",
    )
