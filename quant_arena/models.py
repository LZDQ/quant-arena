"""Domain models for simulation and APIs."""

from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


OrderSide = Literal["buy", "sell"]
OrderStatus = Literal["pending", "filled", "canceled"]


class OrderFill(BaseModel):
    """Execution details that are not already present on the parent order."""

    executed_at: datetime = Field(
        description="Arena-local execution timestamp."
    )
    executed_price: float = Field(
        gt=0,
        description="Execution price in the agent's arena-local currency."
    )
    commission: float = Field(
        ge=0,
        description="Commission charged by the arena's fee policy."
    )
    tax: float = Field(
        ge=0,
        description="Transaction tax or equivalent arena-specific levy charged on the fill."
    )


class OrderRecord(BaseModel):
    """One submitted order."""

    order_id: str = Field(default_factory=lambda: uuid4().hex)
    agent_id: str
    code: str = Field(
        description="Arena-local instrument identifier, such as 600726, US.AAPL, or AAPL.US."
    )
    name: str | None = Field(
        default=None,
        description="Instrument display name resolved by the arena when the order is submitted."
    )
    side: OrderSide = Field(
        description="Order direction: buy acquires the instrument and sell disposes of it."
    )
    quantity: int = Field(
        gt=0,
        description="Positive order quantity; lot-size restrictions are defined by the selected arena."
    )
    limit_price: float = Field(
        gt=0,
        description="Limit price in the agent's arena-local currency."
    )
    comment: str = Field(
        min_length=1,
        max_length=200,
        description="Agent-supplied reason for submitting the order."
    )
    status: OrderStatus = Field(
        default="pending",
        description="Order lifecycle state: pending, filled, or canceled."
    )
    submitted_at: datetime = Field(
        description="Arena-local timestamp when the order was accepted."
    )
    fill: OrderFill | None = Field(
        default=None,
        description="Execution details when the order fills."
    )
    canceled_at: datetime | None = Field(
        default=None,
        description="Cancellation timestamp when the order is canceled."
    )
    rejection_reason: str | None = Field(
        default=None,
        description="Reason an accepted order was later canceled or could not be completed."
    )


class SubmitOrder(BaseModel):
    """Domain request to submit an order."""

    code: str = Field(
        description="Arena-local instrument identifier."
    )
    side: OrderSide = Field(
        description="Order direction: buy or sell."
    )
    quantity: int = Field(
        gt=0,
        description="Positive order quantity; the arena applies its own lot-size rules."
    )
    limit_price: float = Field(
        gt=0,
        description="Limit price in the agent's arena-local currency."
    )
    comment: str = Field(
        min_length=1,
        max_length=200,
        description="Agent-supplied reason for submitting the order."
    )


class ManualPositionClearRecord(BaseModel):
    """One manual position-clear operation triggered from the dashboard."""

    record_id: str = Field(default_factory=lambda: uuid4().hex)
    agent_id: str
    applied_at: datetime = Field(description="操作发生的时间")
    comment: str = Field(
        min_length=1,
        max_length=200,
        description="操作备注，必填",
    )
    keep_unrealized_pnl: bool = Field(
        description="是否保留浮动盈亏（True 时按市场价兑现到现金/已实现盈亏，False 时直接抹掉浮盈/浮亏）"
    )
    keep_realized_pnl: bool = Field(
        description="是否保留已实现盈亏；False 时把已实现盈亏归零并从现金中减去（用于重置回初始金额）"
    )
    cash_before: float = Field(description="操作前现金")
    cash_after: float = Field(description="操作后现金")
    realized_pnl_before: float = Field(description="操作前已实现盈亏")
    realized_pnl_after: float = Field(description="操作后已实现盈亏")
    market_value_before: float = Field(description="被清空持仓的总市值")
    unrealized_pnl_before: float = Field(description="被清空持仓的浮动盈亏")
    cleared_codes: list[str] = Field(
        default_factory=list, description="被清空的持仓代码列表"
    )


class SpecialEvent(BaseModel):
    """A non-trade account event surfaced to the agent and the frontend (e.g. a corporate action)."""

    event_id: str = Field(description="事件唯一标识")
    event_type: Literal["corporate_action", "manual_position_clear"] = Field(
        description="事件类型"
    )
    event_date: date = Field(description="事件发生的交易日（如除权除息日），用于按日期筛选")
    code: str | None = Field(
        default=None,
        description="Related arena-local instrument identifier, or null for account-level events.",
    )
    summary: str = Field(description="渲染好的多行文字说明，前端与 agent 都直接展示这个")
    occurred_at: datetime = Field(description="事件被记录/应用到账户的时间")


class EquityPoint(BaseModel):
    """Daily equity snapshot."""

    trade_date: date = Field(
        description="这条权益快照对应的交易日期"
    )
    cash: float = Field(
        description="当天快照时账户里的现金"
    )
    market_value: float = Field(
        description="当天持仓按市场价格计算出来的市值"
    )
    total_equity: float = Field(
        description="总权益，等于现金加持仓市值"
    )
    realized_pnl: float = Field(
        description="截至当天已经真正实现的盈亏"
    )
    unrealized_pnl: float = Field(
        description="截至当天按最新市场价格计算但尚未卖出兑现的浮动盈亏"
    )


class ArenaAgentState(BaseModel):
    """Persisted account fields shared by every arena."""

    agent_id: str
    cash: float
    realized_pnl: float = 0.0
    orders: list[OrderRecord] = Field(default_factory=list)
    equity_history: list[EquityPoint] = Field(default_factory=list)
    manual_position_clears: list[ManualPositionClearRecord] = Field(
        default_factory=list,
        description="历次手动清仓重置事件",
    )


class PositionSnapshot(BaseModel):
    """Domain view of one portfolio position."""

    code: str
    name: str | None = None
    quantity: int
    sellable_quantity: int
    avg_cost: float | None
    market_price: float | None = None
    market_value: float = 0.0
    unrealized_pnl: float | None = 0.0
    intraday_as_of: datetime | None = None


class PortfolioSnapshot(BaseModel):
    """Domain portfolio snapshot. `currency` is arena-local and may be unset."""

    agent_id: str
    currency: str | None = None
    cash: float
    market_value: float
    total_equity: float
    realized_pnl: float | None
    unrealized_pnl: float | None
    positions: list[PositionSnapshot]
    pending_orders: list[OrderRecord]
    as_of: datetime | None = None
    day_return_pct: float | None = None


class OperationLog(BaseModel):
    """Domain list of orders with inline execution details."""

    orders: list[OrderRecord]


class RankingSnapshot(BaseModel):
    """Domain ranking row. Monetary fields use the arena's own denomination."""

    trade_date: date
    agent_id: str
    display_name: str
    currency: str | None = None
    cash: float
    market_value: float
    total_equity: float
    return_pct: float
    realized_pnl: float
    unrealized_pnl: float


class MonitoredAgentSnapshot(BaseModel):
    """Monitor-agent view of another agent."""

    agent_id: str
    name: str
    display_name: str
    role: Literal["normal", "monitor"]
    currency: str | None = None
    initial_cash: float
    return_pct: float
    portfolio: PortfolioSnapshot


class AgentMetadata(BaseModel):
    """MCP self metadata for the authenticated agent."""

    agent_id: str
    name: str
    display_name: str
    role: Literal["normal", "monitor"]
    currency: str | None = None


class DailyReport(BaseModel):
    """One agent's full daily report for a given trade date."""

    trade_date: date = Field(description="报告对应的日期")
    content: str = Field(description="Markdown 正文")
    updated_at: datetime = Field(description="该报告最近一次写入的时间")


class DailyReportSummary(BaseModel):
    """Lightweight daily-report descriptor used in listings."""

    trade_date: date
    updated_at: datetime
