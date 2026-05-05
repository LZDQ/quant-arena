"""Futumoo offline paper-trading simulator.

Inherits common scaffolding (agent registry, equity curve, daily
reports, persistence) from `BaseArenaService`. The Futumoo-specific
parts are: instant fill at the submitted limit price, the simple
`FutumooPosition` accounting (no T+1 lots), and the daily snapshot
refresh that pulls last prices from Futu OpenD via `FutumooService`.

Symbols are passed through verbatim with their region prefix
(`US.AAPL`, `HK.00700`, `SH.600519`); there is no per-market gating.
"""

import asyncio
from datetime import date, datetime, timezone
from logging import getLogger
from pathlib import Path

from quant_arena.arena_base import BaseArenaService
from quant_arena.config import AgentConfig, FutumooFeeConfig
from quant_arena.errors import BadRequestError
from quant_arena.futumoo.models import FutumooAgentState, FutumooPosition
from quant_arena.futumoo.service import FutumooService
from quant_arena.models import (
    FillRecord,
    OrderRecord,
    PortfolioSnapshot,
    PositionSnapshot,
    SubmitOrder,
)
from quant_arena.notifier import NotifierService

logger = getLogger(__name__)


class FutumooArenaService(BaseArenaService[FutumooAgentState]):
    """Offline paper-trading simulator across all Futu-namespaced markets."""

    def __init__(
        self,
        agents_root: Path,
        market: FutumooService,
        fees: FutumooFeeConfig,
        notifier: NotifierService,
    ):
        super().__init__(
            agents_root=agents_root,
            notifier=notifier,
            state_cls=FutumooAgentState,
        )
        self.market = market
        self.fees = fees
        self._latest_prices: dict[str, float] = {}
        self._snapshot_as_of: datetime | None = None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    # ----- order entry -----

    async def submit_order(
        self,
        agent_id: str,
        request: SubmitOrder,
        submitted_at: datetime | None = None,
    ) -> OrderRecord:
        """Record a submission and instantly fill at the limit price."""
        agent = self.get_agent(agent_id)
        now = submitted_at or self._now()
        if request.quantity <= 0:
            raise BadRequestError("Order quantity must be positive")
        if not request.code:
            raise BadRequestError("Order symbol must not be empty")
        with self._order_lock:
            state = self._state(agent_id)
            order = OrderRecord(
                agent_id=agent_id,
                code=request.code,
                side=request.side,
                quantity=request.quantity,
                limit_price=request.limit_price,
                comment=request.comment,
                submitted_at=now,
                activate_after=now,
            )
            try:
                self._check_can_fill(state, order)
            except BadRequestError as exc:
                order.status = "canceled"
                order.canceled_at = now
                order.rejection_reason = str(exc)
                state.orders.append(order)
                self._save_agent_state(state)
                raise
            self._fill_order(state, order, request.limit_price, now)
            state.orders.append(order)
            self._save_agent_state(state)
        self.notifier.notify_order_submitted(agent, order)
        last_fill = state.fills[-1] if state.fills else None
        if last_fill is not None and last_fill.order_id == order.order_id:
            self.notifier.notify_order_filled(agent, order, last_fill)
        self._rankings_cache = None
        return order

    def _check_can_fill(self, state: FutumooAgentState, order: OrderRecord) -> None:
        notional = float(order.quantity) * float(order.limit_price)
        commission = self._commission(notional)
        if order.side == "buy":
            if state.cash < notional + commission:
                raise BadRequestError(
                    f"Insufficient cash to buy {order.quantity} {order.code} @ {order.limit_price}: "
                    f"need {notional + commission:.2f}, have {state.cash:.2f}"
                )
        else:
            position = state.positions.get(order.code)
            held = position.quantity if position is not None else 0
            if held < order.quantity:
                raise BadRequestError(
                    f"Insufficient quantity to sell {order.quantity} {order.code}: hold {held}"
                )

    def _fill_order(
        self,
        state: FutumooAgentState,
        order: OrderRecord,
        executed_price: float,
        executed_at: datetime,
    ) -> None:
        notional = float(order.quantity) * float(executed_price)
        commission = self._commission(notional)
        fill = FillRecord(
            order_id=order.order_id,
            agent_id=order.agent_id,
            code=order.code,
            side=order.side,
            quantity=order.quantity,
            executed_at=executed_at,
            executed_price=executed_price,
            commission=commission,
            stamp_tax=0.0,
        )
        if order.side == "buy":
            state.cash -= notional + commission
            position = state.positions.get(order.code)
            if position is None or position.quantity == 0:
                effective_cost = (notional + commission) / order.quantity
                state.positions[order.code] = FutumooPosition(
                    quantity=order.quantity,
                    avg_cost=round(effective_cost, 4),
                )
            else:
                new_qty = position.quantity + order.quantity
                new_cost = (
                    position.avg_cost * position.quantity + notional + commission
                ) / new_qty
                state.positions[order.code] = FutumooPosition(
                    quantity=new_qty,
                    avg_cost=round(new_cost, 4),
                )
        else:
            position = state.positions[order.code]
            consumed_cost = position.avg_cost * order.quantity
            state.cash += notional - commission
            state.realized_pnl += (executed_price * order.quantity) - consumed_cost - commission
            new_qty = position.quantity - order.quantity
            if new_qty <= 0:
                del state.positions[order.code]
            else:
                state.positions[order.code] = FutumooPosition(
                    quantity=new_qty,
                    avg_cost=position.avg_cost,
                )
        order.status = "filled"
        order.filled_at = executed_at
        state.fills.append(fill)

    def _commission(self, notional: float) -> float:
        if notional <= 0 or self.fees.commission_bps <= 0:
            return 0.0
        return round(
            max(self.fees.min_commission, notional * self.fees.commission_bps / 10000.0),
            2,
        )

    # ----- portfolio -----

    def _build_portfolio(self, state: FutumooAgentState) -> PortfolioSnapshot:
        positions: list[PositionSnapshot] = []
        market_value = 0.0
        unrealized_pnl = 0.0
        for code, position in sorted(state.positions.items()):
            if position.quantity <= 0:
                continue
            market_price = self._latest_prices.get(code)
            effective_price = market_price if market_price is not None else position.avg_cost
            position_value = effective_price * position.quantity
            position_unrealized = (effective_price - position.avg_cost) * position.quantity
            market_value += position_value
            unrealized_pnl += position_unrealized
            positions.append(
                PositionSnapshot(
                    code=code,
                    quantity=position.quantity,
                    sellable_quantity=position.quantity,
                    avg_cost=round(position.avg_cost, 4),
                    market_price=market_price,
                    market_value=round(position_value, 2),
                    unrealized_pnl=round(position_unrealized, 2),
                )
            )
        total_equity = state.cash + market_value
        pending_orders = [order for order in state.orders if order.status == "pending"]
        return PortfolioSnapshot(
            agent_id=state.agent_id,
            cash=round(state.cash, 2),
            market_value=round(market_value, 2),
            total_equity=round(total_equity, 2),
            realized_pnl=round(state.realized_pnl, 2),
            unrealized_pnl=round(unrealized_pnl, 2),
            positions=positions,
            pending_orders=pending_orders,
            as_of=self._snapshot_as_of,
        )

    # ----- daily equity refresh -----

    def refresh_equity_snapshot(self) -> None:
        """Pull current snapshot prices via Futu OpenD; refresh today's equity points."""
        held: set[str] = set()
        for agent_id, _ in self.list_agents():
            with self._order_lock:
                held |= set(self._state(agent_id).positions.keys())
        if held:
            try:
                prices = self.market.get_snapshot(sorted(held))
            except Exception:
                logger.exception("Futumoo snapshot fetch failed")
                prices = {}
            if prices:
                self._latest_prices.update(prices)
                self._snapshot_as_of = self._now()
        with self._order_lock:
            for agent_id, _ in self.list_agents():
                state = self._state(agent_id)
                self._update_today_equity_point(state)
        self._rankings_cache = None

    def finalize_today(self) -> None:
        """Freeze each agent's today equity point into `equity_history`."""
        with self._order_lock:
            for agent_id, _ in self.list_agents():
                state = self._state(agent_id)
                self._freeze_today_equity(state)
                self._save_agent_state(state)
        self._rankings_cache = None

    async def run(self, polling_interval_seconds: int) -> None:
        """Periodically refresh equity snapshots and finalize once per UTC day."""
        last_finalized: date | None = None
        while True:
            try:
                await asyncio.to_thread(self.refresh_equity_snapshot)
            except Exception:
                logger.exception("Futumoo equity refresh failed")
            today = self._now().date()
            if last_finalized != today:
                try:
                    await asyncio.to_thread(self.finalize_today)
                    last_finalized = today
                except Exception:
                    logger.exception("Futumoo finalize_today failed")
            await asyncio.sleep(polling_interval_seconds)
