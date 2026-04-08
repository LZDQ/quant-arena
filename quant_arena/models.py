"""Domain models for simulation and APIs."""

from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


OrderSide = Literal["buy", "sell"]
OrderStatus = Literal["pending", "filled", "canceled"]


class QuoteSnapshot(BaseModel):
	"""Latest market quote for one code."""

	code: str
	name: str | None = None
	trade_date: date
	as_of: datetime
	last_price: float = Field(gt=0)
	prev_close: float = Field(gt=0)
	limit_up: float = Field(gt=0)
	limit_down: float = Field(gt=0)


class DailyBar(BaseModel):
	"""One daily OHLCV bar."""

	code: str
	trade_date: date
	open_price: float = Field(ge=0)
	high_price: float = Field(ge=0)
	low_price: float = Field(ge=0)
	close_price: float = Field(ge=0)
	prev_close: float = Field(ge=0)
	volume: float = Field(ge=0)
	amount: float = Field(ge=0)


class FiveMinuteBar(BaseModel):
	"""One 5-minute OHLCV bar."""

	code: str
	trade_date: date
	bar_time: datetime
	open_price: float = Field(ge=0)
	high_price: float = Field(ge=0)
	low_price: float = Field(ge=0)
	close_price: float = Field(ge=0)
	volume: float = Field(ge=0)
	amount: float = Field(ge=0)


class PositionLot(BaseModel):
	"""One acquired lot used for T+1 sellability tracking."""

	quantity: int = Field(ge=0)
	acquired_date: date
	cost_price: float = Field(gt=0)


class OrderRecord(BaseModel):
	"""One submitted order."""

	order_id: str = Field(default_factory=lambda: uuid4().hex)
	agent_id: str
	code: str
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
	code: str
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
	agents_root: str
	market_data_root: str


class MarketCodeStatus(BaseModel):
	"""Public market-data status for one code."""

	code: str
	latest_daily_bar_date: date | None = None
	latest_five_minute_bar_date: date | None = None
	five_minute_bar_count: int = 0
	last_five_minute_bar_time: datetime | None = None


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

	code: str
	side: OrderSide
	quantity: int = Field(gt=0)
	limit_price: float = Field(gt=0)
