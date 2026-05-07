"""Persisted state shapes for the HK/US Futumoo paper-trading arena."""

from datetime import date

from pydantic import BaseModel, Field

from quant_arena.models import EquityPoint, FillRecord, OrderRecord


class FutumooPosition(BaseModel):
    """One open long position in a single market.

    HK and US neither enforce T+1 sellability, so a single (qty, avg_cost)
    pair is sufficient — no per-lot tracking.
    """

    quantity: int = Field(ge=0)
    avg_cost: float = Field(ge=0)


class DayTradeRecord(BaseModel):
    """One US day-trade event used by the pattern-day-trader counter.

    A day trade is recorded the first time a (US date, code) pair sees
    both a buy and a sell fill. Subsequent same-day round-trips on the
    same code do not add additional records; this is the deliberately
    lenient simplification documented on `USRegionArena`.
    """

    trade_date: date
    code: str


class FutumooAgentState(BaseModel):
    """Persisted runtime state for one Futumoo (HK + US) paper-trading agent."""

    agent_id: str
    cash: float = Field(
        default=0.0,
        description=(
            "Vestigial single-currency field kept so the base arena's `_state` "
            "fallback constructor stays compatible. The real cash balances are "
            "tracked in `cash_hkd` and `cash_usd`."
        ),
    )
    cash_hkd: float = 0.0
    cash_usd: float = 0.0
    realized_pnl_hkd: float = 0.0
    realized_pnl_usd: float = 0.0
    orders: list[OrderRecord] = Field(default_factory=list)
    fills: list[FillRecord] = Field(default_factory=list)
    positions_hk: dict[str, FutumooPosition] = Field(default_factory=dict)
    positions_us: dict[str, FutumooPosition] = Field(default_factory=dict)
    day_trades: list[DayTradeRecord] = Field(default_factory=list)
    equity_history: list[EquityPoint] = Field(
        default_factory=list,
        description="Kept for base-arena compatibility but not populated; daily history is not persisted on the Futumoo arena.",
    )
