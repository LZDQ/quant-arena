"""FastAPI request and response models."""

from datetime import datetime, date
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


class OperationListResponse(BaseModel):
    """Combined operations payload."""

    orders: list[OrderRecord]
    fills: list[FillRecord]


class PathsResponse(BaseModel):
    """Resolved runtime paths."""

    config_path: str
    agents_root: str
    market_data_root: str


class CreateAgentRequest(BaseModel):
    """Request to create a new agent."""

    agent_id: str
    display_name: str
    initial_cash: float = Field(gt=0)
    sell_constraint: Literal["t_plus_one"] = "t_plus_one"
    enabled: bool = True
    role: Literal["normal", "monitor"] = "normal"

class AgentResponse(BaseModel):
    """API view of one agent plus its directory-based id."""

    agent_id: str
    display_name: str
    initial_cash: float
    sell_constraint: Literal["t_plus_one"]
    enabled: bool
    role: Literal["normal", "monitor"]


class AgentCreatedResponse(BaseModel):
    """Create-agent response with one-time token display."""

    agent: AgentResponse
    token_secret: str


class AgentSnapshotResponse(BaseModel):
    """Grouped frontend view for one agent."""

    agent: AgentResponse
    portfolio: PortfolioResponse
    operations: OperationListResponse
    equity: list[EquityPoint]
