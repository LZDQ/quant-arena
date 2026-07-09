"""EODHD paper-trading arena.

This arena is intentionally independent from Futumoo. EODHD supplies global
market data in `{symbol}.{exchange}` form; the arena keeps one currency per
agent, validates cash/inventory, and matches pending limit orders against
latest `last_price` snapshots.
"""

import asyncio
from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path

from quant_arena.config import AgentConfig, EODHDConfig
from quant_arena.errors import BadRequestError
from quant_arena.eodhd.base import EODHDArenaBase
from quant_arena.eodhd.models import EODHDAgentState, EODHDPosition
from quant_arena.eodhd.service import EODHDService
from quant_arena.models import (
    FillRecord,
    ManualPositionClearRecord,
    OrderRecord,
    PortfolioSnapshot,
    PositionSnapshot,
    SpecialEvent,
    SubmitOrder,
)
from quant_arena.notifier import NotifierService

logger = getLogger(__name__)


SnapshotRow = dict[str, object]


class EODHDArenaService(EODHDArenaBase):
    """Global-symbol paper arena backed by EODHD live snapshots."""

    def __init__(
        self,
        agents_root: Path,
        market: EODHDService,
        config: EODHDConfig,
        notifier: NotifierService,
    ):
        super().__init__(agents_root=agents_root, notifier=notifier)
        self.market = market
        self.config = config
        self._latest_prices: dict[str, float] = {}
        self._code_names: dict[str, str] = {}
        self._snapshot_as_of: datetime | None = None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    # ----- agent registry -----

    def add_agent(self, agent_id: str, agent: AgentConfig) -> AgentConfig:
        allowed = tuple(self.config.allowed_currencies)
        currency = agent.currency or self.config.default_currency
        if currency not in allowed:
            raise BadRequestError(
                f"EODHD agents must use one of {allowed}; got {currency!r}."
            )
        agent.currency = currency
        return super().add_agent(agent_id, agent)

    # ----- order entry -----

    async def submit_order(
        self,
        agent_id: str,
        request: SubmitOrder,
        submitted_at: datetime | None = None,
    ) -> OrderRecord:
        code = self._normalize_code(request.code)
        now = self._coerce_utc(submitted_at) if submitted_at is not None else self._now()
        snapshot = await asyncio.to_thread(self.market.get_snapshots, [code])
        snapshot_row = snapshot.get(code)
        if snapshot_row is None:
            raise BadRequestError(
                f"EODHD returned no live snapshot for {code}; cannot submit."
            )
        with self._order_lock:
            state = self._state(agent_id)
            self._validate_submission(state, request, code, snapshot_row)
            self._absorb_snapshot_names(snapshot)
            order = OrderRecord(
                agent_id=agent_id,
                code=code,
                name=self._code_names.get(code),
                side=request.side,
                quantity=request.quantity,
                limit_price=request.limit_price,
                comment=request.comment,
                submitted_at=now,
                activate_after=now,
            )
            state.orders.append(order)
            self._save_agent_state(state)
            self._latest_prices[code] = self._snapshot_price(snapshot_row)
        self.notifier.notify_order_submitted(self.get_agent(agent_id), order)
        self._rankings_cache = None
        return order

    @staticmethod
    def _normalize_code(code: str) -> str:
        normalized = code.strip()
        if "." not in normalized:
            raise BadRequestError(
                "EODHD symbols must include an exchange suffix, for example AAPL.US."
            )
        return normalized

    @staticmethod
    def _coerce_utc(moment: datetime) -> datetime:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    def _validate_submission(
        self,
        state: EODHDAgentState,
        request: SubmitOrder,
        code: str,
        snapshot_row: SnapshotRow,
    ) -> None:
        self._snapshot_price(snapshot_row)
        if request.side == "buy":
            notional = request.quantity * request.limit_price
            commission, stamp_tax = self._fees_for(notional)
            cost = notional + commission + stamp_tax
            agent = self.get_agent(state.agent_id)
            if state.cash < cost:
                raise BadRequestError(
                    f"Insufficient {agent.currency} cash to buy {request.quantity} "
                    f"{code} @ {request.limit_price}: need {cost:.2f}, "
                    f"have {state.cash:.2f}."
                )
            return
        position = state.positions.get(code)
        held = position.quantity if position is not None else 0
        pending_sell = sum(
            order.quantity
            for order in state.orders
            if order.status == "pending"
            and order.side == "sell"
            and order.code == code
        )
        available = held - pending_sell
        if request.quantity > available:
            raise BadRequestError(
                f"Cannot sell {request.quantity} {code}: hold {held}, "
                f"{pending_sell} already encumbered by other pending sells."
            )

    # ----- matching loop -----

    def match_pending_orders(self) -> None:
        codes: set[str] = set()
        for agent_id, _ in self.list_agents():
            state = self._state(agent_id)
            for order in state.orders:
                if order.status == "pending":
                    codes.add(order.code)
            codes.update(state.positions.keys())
        if not codes:
            return
        try:
            snapshots = self.market.get_snapshots(sorted(codes))
        except Exception:
            logger.exception("EODHD snapshot fetch failed")
            return
        if not snapshots:
            return

        self._absorb_snapshot_names(snapshots)
        max_update: datetime | None = None
        observed_at = self._now()
        for code, row in snapshots.items():
            price = self._snapshot_price(row)
            self._latest_prices[code] = price
            update_at = self._parse_update_time(row.get("update_time")) or observed_at
            if max_update is None or update_at > max_update:
                max_update = update_at
        self._snapshot_as_of = max_update

        with self._order_lock:
            for agent_id, _ in self.list_agents():
                state = self._state(agent_id)
                changed = False
                for order in state.orders:
                    if order.status != "pending":
                        continue
                    row = snapshots.get(order.code)
                    if row is None:
                        continue
                    update_at = self._parse_update_time(row.get("update_time")) or observed_at
                    if update_at <= order.activate_after.astimezone(timezone.utc):
                        continue
                    last_price = self._snapshot_price(row)
                    if order.side == "buy" and last_price > order.limit_price:
                        continue
                    if order.side == "sell" and last_price < order.limit_price:
                        continue
                    if not self._can_still_fill(state, order, last_price):
                        continue
                    fill = self._fill_pending(state, order, last_price, update_at)
                    self.notifier.notify_order_filled(
                        self.get_agent(agent_id), order, fill
                    )
                    changed = True
                if changed:
                    self._update_today_equity_point(state)
                    self._save_agent_state(state)
        self._rankings_cache = None

    def _can_still_fill(
        self,
        state: EODHDAgentState,
        order: OrderRecord,
        market_price: float,
    ) -> bool:
        notional = order.quantity * market_price
        commission, stamp_tax = self._fees_for(notional)
        if order.side == "buy":
            return state.cash >= notional + commission + stamp_tax
        position = state.positions.get(order.code)
        return position is not None and position.quantity >= order.quantity

    def _fill_pending(
        self,
        state: EODHDAgentState,
        order: OrderRecord,
        market_price: float,
        executed_at: datetime,
    ) -> FillRecord:
        notional = order.quantity * market_price
        commission, stamp_tax = self._fees_for(notional)
        if order.side == "buy":
            cost = notional + commission + stamp_tax
            state.cash -= cost
            existing = state.positions.get(order.code)
            if existing is None or existing.quantity == 0:
                state.positions[order.code] = EODHDPosition(
                    quantity=order.quantity,
                    avg_cost=round(cost / order.quantity, 4),
                )
            else:
                new_quantity = existing.quantity + order.quantity
                new_cost = (existing.avg_cost * existing.quantity + cost) / new_quantity
                state.positions[order.code] = EODHDPosition(
                    quantity=new_quantity,
                    avg_cost=round(new_cost, 4),
                )
        else:
            position = state.positions[order.code]
            consumed_cost = position.avg_cost * order.quantity
            proceeds = notional - commission - stamp_tax
            state.cash += proceeds
            state.realized_pnl += proceeds - consumed_cost
            new_quantity = position.quantity - order.quantity
            if new_quantity <= 0:
                del state.positions[order.code]
            else:
                state.positions[order.code] = EODHDPosition(
                    quantity=new_quantity,
                    avg_cost=position.avg_cost,
                )
        order.status = "filled"
        order.filled_at = executed_at
        fill = FillRecord(
            order_id=order.order_id,
            agent_id=order.agent_id,
            code=order.code,
            side=order.side,
            quantity=order.quantity,
            executed_at=executed_at,
            executed_price=market_price,
            commission=commission,
            stamp_tax=stamp_tax,
        )
        state.fills.append(fill)
        return fill

    def _fees_for(self, notional: float) -> tuple[float, float]:
        if notional <= 0 or self.config.fees.commission_bps <= 0:
            return 0.0, 0.0
        commission = max(
            self.config.fees.min_commission,
            notional * self.config.fees.commission_bps / 10000.0,
        )
        return round(commission, 2), 0.0

    @staticmethod
    def _snapshot_price(row: SnapshotRow) -> float:
        raw = row.get("last_price")
        try:
            price = float(raw)
        except (TypeError, ValueError):
            raise BadRequestError(f"EODHD snapshot has no usable last_price: {raw!r}")
        if price <= 0:
            raise BadRequestError(f"EODHD snapshot has non-positive last_price: {price}")
        return price

    def _absorb_snapshot_names(self, snapshots: dict[str, SnapshotRow]) -> None:
        for code, row in snapshots.items():
            raw = row.get("name")
            if raw is None:
                continue
            text = str(raw).strip()
            if text:
                self._code_names[code] = text

    @staticmethod
    def _parse_update_time(raw: object) -> datetime | None:
        if raw is None:
            return None
        if isinstance(raw, int | float):
            return datetime.fromtimestamp(float(raw), timezone.utc)
        text = str(raw).strip()
        if not text:
            return None
        try:
            if text.isdigit():
                return datetime.fromtimestamp(float(text), timezone.utc)
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    # ----- special events -----

    def _special_events(self, state: EODHDAgentState) -> list[SpecialEvent]:
        return [
            self._render_manual_clear_event(record)
            for record in state.manual_position_clears
        ]

    @staticmethod
    def _render_manual_clear_event(record: ManualPositionClearRecord) -> SpecialEvent:
        kept_unrealized = "kept" if record.keep_unrealized_pnl else "wiped"
        kept_realized = "kept" if record.keep_realized_pnl else "wiped"
        codes = ", ".join(record.cleared_codes) if record.cleared_codes else "none"
        lines = [
            f"Manual position clear: note {record.comment!r}",
            f"Cash {record.cash_before:.2f} -> {record.cash_after:.2f}",
            f"Realized P&L {record.realized_pnl_before:.2f} -> {record.realized_pnl_after:.2f} ({kept_realized})",
            f"Unrealized P&L {record.unrealized_pnl_before:.2f} ({kept_unrealized}), cleared market value {record.market_value_before:.2f}",
            f"Cleared positions: {codes}",
        ]
        return SpecialEvent(
            event_id=record.record_id,
            event_type="manual_position_clear",
            event_date=record.applied_at.date(),
            code=None,
            summary="\n".join(lines),
            occurred_at=record.applied_at,
        )

    # ----- portfolio -----

    def _build_portfolio(self, state: EODHDAgentState) -> PortfolioSnapshot:
        positions: list[PositionSnapshot] = []
        market_value = 0.0
        unrealized_pnl = 0.0
        for code, position in sorted(state.positions.items()):
            if position.quantity <= 0:
                continue
            market_price = self._latest_prices.get(code)
            effective_price = (
                market_price if market_price is not None else position.avg_cost
            )
            position_value = effective_price * position.quantity
            position_unrealized = (
                effective_price - position.avg_cost
            ) * position.quantity
            market_value += position_value
            unrealized_pnl += position_unrealized
            positions.append(
                PositionSnapshot(
                    code=code,
                    name=self._code_names.get(code),
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
        agent = self._agents.get(state.agent_id)
        currency = agent.currency if agent is not None else self.config.default_currency
        return PortfolioSnapshot(
            agent_id=state.agent_id,
            currency=currency,
            cash=round(state.cash, 2),
            market_value=round(market_value, 2),
            total_equity=round(total_equity, 2),
            realized_pnl=round(state.realized_pnl, 2),
            unrealized_pnl=round(unrealized_pnl, 2),
            positions=positions,
            pending_orders=pending_orders,
            as_of=self._snapshot_as_of,
        )

    def refresh_portfolio_prices(self) -> None:
        held: set[str] = set()
        for agent_id, _ in self.list_agents():
            state = self._state(agent_id)
            held.update(state.positions.keys())
        if held:
            try:
                snapshots = self.market.get_snapshots(sorted(held))
            except Exception:
                logger.exception("EODHD portfolio snapshot refresh failed")
                snapshots = {}
            self._absorb_snapshot_names(snapshots)
            observed_at = self._now()
            max_update: datetime | None = None
            for code, row in snapshots.items():
                self._latest_prices[code] = self._snapshot_price(row)
                update_at = self._parse_update_time(row.get("update_time")) or observed_at
                if max_update is None or update_at > max_update:
                    max_update = update_at
            if max_update is not None:
                self._snapshot_as_of = max_update
        with self._order_lock:
            for agent_id, _ in self.list_agents():
                self._update_today_equity_point(self._state(agent_id))

    async def run(self, polling_interval_seconds: int) -> None:
        while True:
            try:
                await asyncio.to_thread(self.match_pending_orders)
            except Exception:
                logger.exception("EODHD match cycle failed")
            try:
                await asyncio.to_thread(self.refresh_portfolio_prices)
            except Exception:
                logger.exception("EODHD portfolio price refresh failed")
            await asyncio.sleep(polling_interval_seconds)
