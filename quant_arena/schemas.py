"""FastAPI request and response models."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from quant_arena.models import CodeNameEntry, DailyBar, FillRecord, FiveMinuteBar, OrderRecord


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


class MarketCodeStatus(BaseModel):
    """Public market-data status for one code."""

    code: str
    latest_daily_bar_date: date | None = None
    latest_five_minute_bar_date: date | None = None
    five_minute_bar_count: int = 0
    last_five_minute_bar_time: datetime | None = None


class CodeSearchResponse(BaseModel):
    """Paged code-directory response."""

    query: str
    page: int
    page_size: int
    total: int
    items: list[CodeNameEntry]
    last_refreshed_at: datetime | None = None
    auto_refresh_enabled: bool


class CodeRefreshResponse(BaseModel):
    """Result of a code-directory refresh."""

    refreshed_at: datetime
    entry_count: int


class MarketStatusResponse(BaseModel):
    """Public market-data overview."""

    tracked_codes: list[str]
    codes: list[MarketCodeStatus]


class MarketBarsResponse(BaseModel):
    """Public market-data payload for one code/date."""

    code: str
    trade_date: date
    daily_bar: DailyBar | None = None
    five_minute_bars: list[FiveMinuteBar] = Field(default_factory=list)


class MarketParseResponse(BaseModel):
    """Result of a manual market-data parse attempt."""

    trade_date: date
    tracked_codes: list[str]
    parsed_daily_codes: list[str]
    parsed_five_minute_codes: list[str]


class MarketRangeParseRequest(BaseModel):
    """Request to parse market data across a date range for tracked codes."""

    start_date: date
    end_date: date


class MarketParseJobResponse(BaseModel):
    """In-memory long-running market parse job state."""

    job_id: str
    status: Literal["pending", "running", "completed", "failed"]
    start_date: date
    end_date: date
    tracked_codes_total: int
    tracked_codes_completed: int
    current_code: str | None = None
    current_step: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    daily_rows_written: int = 0
    five_minute_rows_written: int = 0
    skipped_daily_codes: int = 0
    skipped_five_minute_codes: int = 0
    message: str | None = None
    error: str | None = None


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
