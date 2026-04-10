"""FastAPI request and response models."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from quant_arena.models import EquityPoint, FillRecord, OrderRecord


class PositionView(BaseModel):
    """API view of one portfolio position."""

    code: str
    quantity: int
    sellable_quantity: int
    avg_cost: float
    market_price: float | None = None
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


class PortfolioResponse(BaseModel):
    """Portfolio plus pending orders."""

    agent_id: str
    cash: float
    market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    positions: list[PositionView]
    pending_orders: list[OrderRecord]
    as_of: datetime | None = None


class RankingEntry(BaseModel):
    """One ranking row."""

    trade_date: date
    agent_id: str
    display_name: str
    total_equity: float
    return_pct: float
    realized_pnl: float
    unrealized_pnl: float


class OperationListResponse(BaseModel):
    """Combined operations payload."""

    orders: list[OrderRecord]
    fills: list[FillRecord]


class PathsResponse(BaseModel):
    """Resolved runtime paths."""

    config_path: str
    agents_root: str
    market_data_root: str


class CodeSearchItem(BaseModel):
    code: str
    name: str


class CodeSearchResponse(BaseModel):
    """Paged code-directory response."""

    query: str
    page: int
    page_size: int
    total: int
    items: list[CodeSearchItem]
    last_refreshed_at: datetime | None = None
    auto_refresh_enabled: bool


class CodeRefreshResponse(BaseModel):
    """Result of a code-directory refresh."""

    refreshed_at: datetime
    entry_count: int


class MarketParseResponse(BaseModel):
    """Result of a manual market-data parse attempt."""

    trade_date: date
    tracked_codes: list[str]
    parsed_daily_codes: list[str]
    parsed_five_minute_codes: list[str]


class CreateAgentRequest(BaseModel):
    """Request to create a new agent."""

    agent_id: str
    display_name: str
    token_secret: str
    initial_cash: float = Field(gt=0)
    sell_constraint: Literal["t_plus_one"] = "t_plus_one"
    enabled: bool = True


class UpdateAgentRequest(BaseModel):
    """Request to replace mutable agent config fields."""

    display_name: str | None = None
    token_secret: str | None = None
    initial_cash: float | None = Field(default=None, gt=0)
    sell_constraint: Literal["t_plus_one"] | None = None
    enabled: bool | None = None


class AgentResponse(BaseModel):
    """API view of one agent plus its directory-based id."""

    agent_id: str
    display_name: str
    token_secret: str
    initial_cash: float
    sell_constraint: Literal["t_plus_one"]
    enabled: bool


class SubmitOrderRequest(BaseModel):
    """Submit a pending order."""

    code: str
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    limit_price: float = Field(gt=0)


class AgentSnapshotResponse(BaseModel):
    """Grouped frontend view for one agent."""

    agent: AgentResponse
    portfolio: PortfolioResponse
    operations: OperationListResponse
    equity: list[EquityPoint]
