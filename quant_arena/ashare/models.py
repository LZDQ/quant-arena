"""Persisted state and corporate-action models for the A-share arena."""

from datetime import date, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from quant_arena.models import ArenaAgentState, OrderSide, SubmitOrder


class AShareSubmitOrder(SubmitOrder):
    """A-share order request with an explicit execution-session choice."""

    next_open: bool = Field(
        description=(
            "False submits an ordinary same-session order; True queues the "
            "request for the next trading day's opening auction. Next-open is "
            "intended for sell orders; buy orders are accepted but not recommended."
        )
    )


class NextOpenOrder(BaseModel):
    """Persisted A-share request waiting to become a normal order at the open."""

    order_id: str = Field(default_factory=lambda: uuid4().hex)
    agent_id: str
    code: str = Field(description="Six-digit A-share instrument identifier.")
    name: str | None = Field(
        default=None,
        description="Instrument display name resolved when the request is queued.",
    )
    side: OrderSide
    quantity: int = Field(gt=0)
    limit_price: float = Field(gt=0)
    comment: str = Field(min_length=1, max_length=200)
    submitted_at: datetime = Field(
        description="Shanghai-local timestamp when the next-open request was accepted."
    )
    scheduled_for: date = Field(
        description="Trading date whose 09:25 opening-auction price will be used."
    )


class PositionLot(BaseModel):
    """One acquired lot used for T+1 sellability tracking."""

    quantity: int = Field(ge=0)
    acquired_date: date
    cost_price: float = Field(gt=0)


class CorporateAction(BaseModel):
    """One scheduled A-share dividend or share-distribution event."""

    code: str = Field(description="发生分红送转的股票代码")
    register_date: date = Field(description="股权登记日，按这一天收盘的持仓享有权益")
    ex_date: date = Field(description="除权除息日")
    cash_per_share_pretax: float = Field(
        ge=0.0, description="每股现金分红（税前），对应 baostock dividCashPsBeforeTax"
    )
    bonus_shares_per_share: float = Field(
        ge=0.0, description="每股送红股数（来自留存收益），对应 baostock dividStocksPs"
    )
    reserve_shares_per_share: float = Field(
        ge=0.0, description="每股资本公积转增股数，对应 baostock dividReserveToStockPs"
    )
    scheme: str = Field(default="", description="分红送转方案的文字描述，如 10转4派1元（含税）")


class CorporateActionRecord(BaseModel):
    """One A-share corporate action already applied to an agent."""

    record_id: str = Field(default_factory=lambda: uuid4().hex)
    agent_id: str
    code: str = Field(description="发生分红送转的股票代码")
    ex_date: date = Field(description="除权除息日")
    register_date: date = Field(description="股权登记日")
    scheme: str = Field(default="", description="分红送转方案的文字描述")
    shares_before: int = Field(ge=0, description="除权除息前持有股数（等于登记日收盘持仓）")
    bonus_shares: int = Field(ge=0, description="本次新增股数（送红股+资本公积转增，按整股向下取整）")
    shares_after: int = Field(ge=0, description="除权除息后持有股数")
    cost_price_before: float = Field(gt=0, description="除权除息前的平均成本价")
    cost_price_after: float = Field(gt=0, description="按总成本不变摊薄到新股数后的平均成本价")
    cash_dividend_gross: float = Field(ge=0, description="税前现金分红总额")
    dividend_tax: float = Field(
        ge=0, description="按财税2015年101号差别化税率逐 lot 代扣的红利税总额"
    )
    cash_dividend_net: float = Field(ge=0, description="实际到账现金分红（税前减税）")
    fractional_cash: float = Field(
        ge=0, description="不足一股的碎股按除权参考价折算成的现金"
    )
    applied_at: datetime = Field(description="该事件被应用到账户的时间")


class AShareAgentState(ArenaAgentState):
    """Persisted runtime state for one A-share agent."""

    positions: dict[str, list[PositionLot]] = Field(
        default_factory=dict,
        description="持仓",
    )
    corporate_actions: list[CorporateActionRecord] = Field(
        default_factory=list,
        description="已应用的分红送转事件",
    )
    next_open_orders: list[NextOpenOrder] = Field(
        default_factory=list,
        description="等待在下一交易日开盘集合竞价转换为普通订单的请求",
    )
