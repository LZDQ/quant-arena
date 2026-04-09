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
    last_checked_at: datetime | None = Field(
        default=None,
        description="最近一次用市场数据检查这笔订单是否可成交的时间"
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

class CodeNameEntry(BaseModel):
    """Reference row for one market code."""

    code: str
    name: str

class DataParserJobConfig(BaseModel):
    """Config of a data parser job for a date range."""

    mode: Literal["daily", "five_minute", "both"]
    start_date: date
    end_date: date
    skip_existing: bool

class DataParserJobEntry(BaseModel):
    """An entry to a data parser job."""

    config: DataParserJobConfig
    skipped: int | None = Field(description="Number of skipped code, if skip_existing is set")
    parsed: int = Field(description="Number of codes processed")
    error: str | None
    start_time: datetime = Field(description="The time this job started")
    finish_time: datetime | None = Field(description="If this job finished or errored, the time it finished")
