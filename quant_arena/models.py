"""Domain models for simulation and APIs."""

from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


OrderSide = Literal["buy", "sell"]
OrderStatus = Literal["pending", "filled", "canceled"]


class PositionLot(BaseModel):
    """One acquired lot used for T+1 sellability tracking."""

    quantity: int = Field(ge=0)
    acquired_date: date
    cost_price: float = Field(gt=0)


class OrderRecord(BaseModel):
    """One submitted order."""

    order_id: str = Field(default_factory=lambda: uuid4().hex)
    agent_id: str
    code: str = Field(
        description="股票代码，不含前缀，如 600726"
    )
    side: OrderSide = Field(
        description="买卖方向，buy 是买入，sell 是卖出"
    )
    quantity: int = Field(
        gt=0,
        description="委托数量，买入时需要是 100 的倍数"
    )
    limit_price: float = Field(
        gt=0,
        description="限价单价格，只有市场价格达到这个条件才会成交"
    )
    comment: str = Field(
        min_length=1,
        max_length=200,
        description="下单原因备注"
    )
    status: OrderStatus = Field(
        default="pending",
        description="订单状态，pending 是待成交，filled 是已成交，canceled 是已撤销"
    )
    submitted_at: datetime = Field(
        description="下单时间"
    )
    activate_after: datetime = Field(
        description="订单最早可被撮合检查的时间，用来避免刚提交就被同一时刻的数据立刻成交"
    )
    filled_at: datetime | None = Field(
        default=None,
        description="订单实际成交时间"
    )
    canceled_at: datetime | None = Field(
        default=None,
        description="订单撤销时间"
    )
    rejection_reason: str | None = Field(
        default=None,
        description="如果因为交易规则或资金仓位限制导致不能成交，这里记录原因"
    )


class SubmitOrder(BaseModel):
    """Domain request to submit an order."""

    code: str = Field(
        description="股票代码"
    )
    side: OrderSide = Field(
        description="买卖方向，buy 是买入，sell 是卖出"
    )
    quantity: int = Field(
        gt=0,
        description="委托数量"
    )
    limit_price: float = Field(
        gt=0,
        description="限价单价格，只有市场价格达到这个条件才会成交"
    )
    comment: str = Field(
        min_length=1,
        max_length=200,
        description="下单原因备注"
    )


class FillRecord(BaseModel):
    """One executed fill."""

    fill_id: str = Field(default_factory=lambda: uuid4().hex)
    order_id: str
    agent_id: str
    code: str = Field(
        description="成交对应的股票代码"
    )
    side: OrderSide = Field(
        description="成交方向，buy 是买入，sell 是卖出"
    )
    quantity: int = Field(
        gt=0,
        description="实际成交数量"
    )
    executed_at: datetime = Field(
        description="成交时间"
    )
    executed_price: float = Field(
        gt=0,
        description="实际成交价格"
    )
    commission: float = Field(
        ge=0,
        description="这笔成交收取的手续费"
    )
    stamp_tax: float = Field(
        ge=0,
        description="这笔成交收取的印花税，通常只在卖出时有"
    )


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


class PositionSnapshot(BaseModel):
    """Domain view of one portfolio position."""

    code: str
    quantity: int
    sellable_quantity: int
    avg_cost: float
    market_price: float | None = None
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


class PortfolioSnapshot(BaseModel):
    """Domain portfolio snapshot."""

    agent_id: str
    cash: float
    market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    positions: list[PositionSnapshot]
    pending_orders: list[OrderRecord]
    as_of: datetime | None = None
    cash_breakdown: dict[str, float] | None = Field(
        default=None,
        description="Per-currency cash, e.g. {\"HKD\": 80000, \"USD\": 10000} on the futumoo arena. None on single-currency arenas.",
    )
    market_value_breakdown: dict[str, float] | None = Field(
        default=None,
        description="Per-currency market value. None on single-currency arenas.",
    )


class OperationLog(BaseModel):
    """Domain list of orders and fills."""

    orders: list[OrderRecord]
    fills: list[FillRecord]


class RankingSnapshot(BaseModel):
    """Domain ranking row."""

    trade_date: date
    agent_id: str
    display_name: str
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
    portfolio: PortfolioSnapshot


class AgentMetadata(BaseModel):
    """MCP self metadata for the authenticated agent."""

    agent_id: str
    name: str
    display_name: str
    role: Literal["normal", "monitor"]


class DailyReport(BaseModel):
    """One agent's full daily report for a given trade date."""

    trade_date: date = Field(description="报告对应的日期")
    content: str = Field(description="Markdown 正文")
    updated_at: datetime = Field(description="该报告最近一次写入的时间")


class DailyReportSummary(BaseModel):
    """Lightweight daily-report descriptor used in listings."""

    trade_date: date
    updated_at: datetime


class AgentState(BaseModel):
    """Private persisted runtime state for one agent."""

    agent_id: str
    cash: float
    realized_pnl: float = Field(
        0.0, description="盈亏"
    )
    orders: list[OrderRecord] = Field(
        default_factory=list,
        description="挂单，可能成交也可能还没"
    )
    fills: list[FillRecord] = Field(
        default_factory=list,
        description="成交的挂单"
    )
    positions: dict[str, list[PositionLot]] = Field(
        default_factory=dict,
        description="持仓"
    )
    equity_history: list[EquityPoint] = Field(
        default_factory=list,
        description="历史盈亏记录"
    )
