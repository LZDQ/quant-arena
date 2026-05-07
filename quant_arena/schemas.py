"""FastAPI request and response models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from quant_arena.models import DailyReportSummary, EquityPoint, FillRecord, OrderRecord


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
    """Portfolio plus pending orders. All monetary fields are in the agent's currency."""

    agent_id: str
    cash: float
    market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    positions: list[PositionView]
    pending_orders: list[OrderRecord]
    as_of: datetime | None = None


class OperationListResponse(BaseModel):
    """Combined operations payload."""

    orders: list[OrderRecord]
    fills: list[FillRecord]


class PathsResponse(BaseModel):
    """Resolved runtime paths."""

    config_path: str
    agents_root: str
    market_data_root: str


AgentCurrency = Literal["CNY", "HKD", "USD"]


class CreateAgentRequest(BaseModel):
    """Request to create a new agent.

    `currency` selects the single denomination the agent will trade in.
    A-share accepts only `CNY`; Futumoo accepts `HKD` or `USD`. The arena
    rejects any other combination at submission time.
    """

    agent_id: str
    display_name: str
    initial_cash: float = Field(gt=0)
    currency: AgentCurrency = "CNY"
    enabled: bool = True
    role: Literal["normal", "monitor"] = "normal"


class AgentResponse(BaseModel):
    """API view of one agent plus its directory-based id."""

    agent_id: str
    display_name: str
    initial_cash: float
    currency: AgentCurrency
    enabled: bool
    role: Literal["normal", "monitor"]


class AgentCreatedResponse(BaseModel):
    """Create-agent response with one-time token display."""

    agent: AgentResponse
    token_secret: str


class DailyReportPage(BaseModel):
    """Paginated listing of an agent's daily reports (newest first)."""

    items: list[DailyReportSummary]
    total: int
    page: int
    page_size: int


class AgentSnapshotResponse(BaseModel):
    """Grouped frontend view for one agent."""

    agent: AgentResponse
    portfolio: PortfolioResponse
    operations: OperationListResponse
    equity: list[EquityPoint]
