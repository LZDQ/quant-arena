"""Persisted state shapes for the EODHD paper-trading arena."""

from datetime import date, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from quant_arena.models import ArenaAgentState, EquityPoint


class EODHDPosition(BaseModel):
    """One open long position.

    EODHD is used here as a data source instead of a broker-region rule engine,
    so a single quantity / average-cost pair is enough.
    """

    quantity: int = Field(ge=0)
    avg_cost: float = Field(ge=0)


class EODHDCorporateActionRecord(BaseModel):
    """One EODHD split/dividend event already applied to one agent position."""

    record_id: str = Field(default_factory=lambda: uuid4().hex)
    agent_id: str
    code: str
    exchange: str
    ex_date: date
    scheme: str = ""
    split_ratio: float = Field(gt=0)
    cash_dividend_per_share: float = Field(ge=0)
    dividend_currency: str | None = None
    shares_before: int = Field(ge=0)
    shares_after: int = Field(ge=0)
    share_delta: int
    avg_cost_before: float = Field(ge=0)
    avg_cost_after: float = Field(ge=0)
    cash_dividend_gross: float = Field(ge=0)
    cash_dividend_net: float = Field(ge=0)
    fractional_shares: float = Field(ge=0)
    fractional_cash: float = Field(ge=0)
    applied_at: datetime


class EODHDAgentState(ArenaAgentState):
    """Persisted runtime state for one EODHD paper-trading agent.

    The agent's currency is a property of `AgentConfig`; every monetary field
    below is in that single configured currency.
    """

    positions: dict[str, EODHDPosition] = Field(default_factory=dict)
    equity_history: list[EquityPoint] = Field(
        default_factory=list,
        description="Daily equity history plus the current in-memory point rendered by the dashboard.",
    )
    corporate_actions: list[EODHDCorporateActionRecord] = Field(
        default_factory=list,
        description="Applied EODHD split/dividend events.",
    )
