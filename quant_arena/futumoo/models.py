"""Persisted state shapes for the Futumoo paper-trading arena."""

from datetime import date

from pydantic import BaseModel, Field

from quant_arena.models import ArenaAgentState, EquityPoint


class FutumooPosition(BaseModel):
    """One open long position. Futumoo regions do not enforce T+1 sellability,
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


class FutumooAgentState(ArenaAgentState):
    """Persisted runtime state for one Futumoo paper-trading agent.

    The agent's currency is a property of `AgentConfig`, not of the state
    itself; every monetary field below is in that single currency.
    """

    positions: dict[str, FutumooPosition] = Field(default_factory=dict)
    day_trades: list[DayTradeRecord] = Field(default_factory=list)
    equity_history: list[EquityPoint] = Field(
        default_factory=list,
        description="Reserved for future persisted daily history; currently the Futumoo arena refreshes only today's in-memory equity point.",
    )
