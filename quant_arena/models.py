"""Domain models for simulation and APIs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


OrderSide = Literal["buy", "sell"]
OrderStatus = Literal["pending", "filled", "canceled"]


class QuoteSnapshot(BaseModel):
	"""Latest market quote for one symbol."""

	symbol: str
	name: str | None = None
	trade_date: date
	as_of: datetime
	last_price: float = Field(gt=0)
	prev_close: float = Field(gt=0)
	limit_up: float = Field(gt=0)
	limit_down: float = Field(gt=0)


class PositionLot(BaseModel):
	"""One acquired lot used for T+1 sellability tracking."""

	quantity: int = Field(ge=0)
	acquired_date: date
	cost_price: float = Field(gt=0)


class OrderRecord(BaseModel):
	"""One submitted order."""

	order_id: str = Field(default_factory=lambda: uuid4().hex)
	agent_id: str
	symbol: str
	side: OrderSide
	quantity: int = Field(gt=0)
	limit_price: float = Field(gt=0)
	status: OrderStatus = "pending"
	submitted_at: datetime
	activate_after: datetime
	last_checked_at: datetime | None = None
	filled_at: datetime | None = None
	canceled_at: datetime | None = None
	rejection_reason: str | None = None


class FillRecord(BaseModel):
	"""One executed fill."""

	fill_id: str = Field(default_factory=lambda: uuid4().hex)
	order_id: str
	agent_id: str
	symbol: str
	side: OrderSide
	quantity: int = Field(gt=0)
	executed_at: datetime
	executed_price: float = Field(gt=0)
	commission: float = Field(ge=0)
	stamp_tax: float = Field(ge=0)


class EquityPoint(BaseModel):
	"""Daily equity snapshot."""

	date: date
	cash: float
	market_value: float
	total_equity: float
	realized_pnl: float
	unrealized_pnl: float


class AgentState(BaseModel):
	"""Private persisted runtime state for one agent."""

	agent_id: str
	cash: float
	realized_pnl: float = 0.0
	orders: list[OrderRecord] = Field(default_factory=list)
	fills: list[FillRecord] = Field(default_factory=list)
	positions: dict[str, list[PositionLot]] = Field(default_factory=dict)
	equity_history: list[EquityPoint] = Field(default_factory=list)


class PositionView(BaseModel):
	"""API view of one portfolio position."""

	symbol: str
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

	date: date
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
	project_root: str
	market_data_root: str
	agents_config_path: str


class CreateAgentRequest(BaseModel):
	"""Request to create a new agent."""

	agent_id: str
	display_name: str
	token_header_name: str = "X-Agent-Token"
	token_secret: str
	initial_cash: float = Field(gt=0)
	sell_constraint: Literal["t_plus_one"] = "t_plus_one"
	active: bool = True


class UpdateAgentRequest(BaseModel):
	"""Request to replace mutable agent config fields."""

	display_name: str | None = None
	token_header_name: str | None = None
	token_secret: str | None = None
	initial_cash: float | None = Field(default=None, gt=0)
	sell_constraint: Literal["t_plus_one"] | None = None
	active: bool | None = None


class SubmitOrderRequest(BaseModel):
	"""Submit a pending order."""

	symbol: str
	side: OrderSide
	quantity: int = Field(gt=0)
	limit_price: float = Field(gt=0)


class MCPRequest(BaseModel):
	"""Minimal JSON-RPC style MCP request."""

	jsonrpc: str = "2.0"
	id: str | int | None = None
	method: str
	params: dict[str, Any] | None = None


class MCPResponse(BaseModel):
	"""Minimal JSON-RPC style MCP response."""

	jsonrpc: str = "2.0"
	id: str | int | None = None
	result: Any | None = None
	error: dict[str, Any] | None = None
