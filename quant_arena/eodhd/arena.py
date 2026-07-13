"""EODHD paper-trading arena.

This arena is intentionally independent from Futumoo. EODHD supplies global
market data in `{symbol}.{exchange}` form; the arena keeps one currency per
agent, validates cash/inventory, and matches pending limit orders directly from
websocket `last_price` events.
"""

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from logging import getLogger
from math import floor
from pathlib import Path

from quant_arena.arena import ArenaBase
from quant_arena.config import AgentConfig, EODHDConfig
from quant_arena.errors import BadRequestError
from quant_arena.eodhd.models import (
    EODHDAgentState,
    EODHDCorporateActionRecord,
    EODHDPosition,
)
from quant_arena.eodhd.service import EODHDCorporateAction, EODHDService
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


class EODHDArenaService(ArenaBase[EODHDAgentState]):
    """Global-symbol paper arena backed by EODHD live snapshots."""

    def __init__(
        self,
        agents_root: Path,
        market: EODHDService,
        config: EODHDConfig,
        notifier: NotifierService,
    ):
        super().__init__(
            agents_root=agents_root,
            notifier=notifier,
            state_type=EODHDAgentState,
        )
        self.market = market
        self.config = config
        self._latest_prices: dict[str, float] = {}
        self._code_names: dict[str, str] = {}
        self._snapshot_as_of: datetime | None = None
        self.market.set_live_quote_handler(self._handle_live_quote)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _clear_positions(self, state: EODHDAgentState) -> None:
        state.positions.clear()

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
        if not self.market.is_symbol_exchange_enabled(code):
            exchange = code.rsplit(".", 1)[1].upper()
            raise BadRequestError(
                f"EODHD exchange {exchange!r} is disabled; new buy and sell "
                "orders for that exchange are not allowed."
            )
        now = self._coerce_utc(submitted_at) if submitted_at is not None else self._now()
        snapshot = await asyncio.to_thread(self.market.get_snapshots, [code])
        snapshot_row = snapshot.get(code)
        if snapshot_row is None:
            if not self.market.is_websocket_live_quote_supported(code):
                raise BadRequestError(
                    "EODHD websocket live quotes support US equities, FOREX, "
                    f"and crypto symbols only; got {code}."
                )
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
            self.market.subscribe_live_quotes([code])
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

    # ----- websocket matching -----

    def _subscribe_tracked_symbols(self) -> None:
        codes: set[str] = set()
        for agent_id, _ in self.list_agents():
            state = self._state(agent_id)
            for order in state.orders:
                if order.status == "pending":
                    codes.add(order.code)
            codes.update(state.positions.keys())
        self.market.subscribe_live_quotes(sorted(codes))

    def _handle_live_quote(self, code: str, row: SnapshotRow) -> None:
        observed_at = self._now()
        last_price = self._snapshot_price(row)
        update_at = self._parse_update_time(row.get("update_time")) or observed_at

        with self._order_lock:
            self._latest_prices[code] = last_price
            if self._snapshot_as_of is None or update_at > self._snapshot_as_of:
                self._snapshot_as_of = update_at
            for agent_id, _ in self.list_agents():
                state = self._state(agent_id)
                changed = False
                for order in state.orders:
                    if order.status != "pending" or order.code != code:
                        continue
                    if update_at <= order.activate_after.astimezone(timezone.utc):
                        continue
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
        events: list[SpecialEvent] = []
        for record in state.corporate_actions:
            events.append(
                SpecialEvent(
                    event_id=record.record_id,
                    event_type="corporate_action",
                    event_date=record.ex_date,
                    code=record.code,
                    summary=self._render_corporate_action(
                        record, self.market.get_code_name(record.code)
                    ),
                    occurred_at=record.applied_at,
                )
            )
        for record in state.manual_position_clears:
            events.append(self._render_manual_clear_event(record))
        return events

    @staticmethod
    def _render_corporate_action(
        record: EODHDCorporateActionRecord, name: str | None
    ) -> str:
        label = f"{record.code} ({name})" if name else record.code
        lines = [
            f"EODHD corporate action {label} on {record.ex_date.isoformat()}: "
            f"{record.scheme or 'split/dividend'}",
            f"Position {record.shares_before} -> {record.shares_after} shares",
            f"Avg cost {record.avg_cost_before:.4f} -> {record.avg_cost_after:.4f}",
        ]
        if abs(record.split_ratio - 1.0) > 0.000000001:
            lines.append(f"Split ratio {record.split_ratio:.8g}")
        if record.cash_dividend_gross > 0.0:
            currency = f"{record.dividend_currency} " if record.dividend_currency else ""
            lines.append(
                f"Cash dividend +{currency}{record.cash_dividend_gross:.2f} "
                f"(net {currency}{record.cash_dividend_net:.2f})"
            )
        if record.fractional_cash > 0.0:
            currency = f"{record.dividend_currency} " if record.dividend_currency else ""
            lines.append(
                f"Fractional shares {record.fractional_shares:.6f} "
                f"cashed out +{currency}{record.fractional_cash:.2f}"
            )
        return "\n".join(lines)

    def apply_corporate_actions(self, ex_date: date) -> None:
        """
        Apply EODHD split/dividend events for held positions on ``ex_date``.

        EODHD provides split/dividend data as exchange/date bulk rows. The
        service fetches only exchanges represented by current holdings, then
        this method applies matching events idempotently per agent/code/date.
        """
        held_codes: set[str] = set()
        for agent_id, _ in self.list_agents():
            state = self._state(agent_id)
            held_codes |= {
                code
                for code, position in state.positions.items()
                if position.quantity > 0
            }
        if not held_codes:
            logger.info("No EODHD holdings for corporate-action scan on %s", ex_date)
            return

        actions = self.market.fetch_corporate_actions(ex_date, held_codes)
        if not actions:
            logger.info("No EODHD corporate actions for held positions on %s", ex_date)
            return

        actions_by_code = {action.code: action for action in actions}
        timestamp = self._now()
        applied_count = 0
        with self._order_lock:
            for agent_id, _ in self.list_agents():
                state = self._state(agent_id)
                changed = False
                for code, action in actions_by_code.items():
                    position = state.positions.get(code)
                    if position is None or position.quantity <= 0:
                        continue
                    already_applied = any(
                        record.code == code and record.ex_date == ex_date
                        for record in state.corporate_actions
                    )
                    if already_applied:
                        continue
                    self._apply_one_corporate_action(state, action, timestamp)
                    changed = True
                    applied_count += 1
                if changed:
                    self._update_today_equity_point(state)
                    self._save_agent_state(state)
        if applied_count > 0:
            self._rankings_cache = None
        logger.info(
            "Applied EODHD corporate actions for %s (agent-position events=%d)",
            ex_date,
            applied_count,
        )

    def _apply_one_corporate_action(
        self,
        state: EODHDAgentState,
        action: EODHDCorporateAction,
        timestamp: datetime,
    ) -> None:
        position = state.positions[action.code]
        shares_before = position.quantity
        avg_cost_before = position.avg_cost

        split_ratio = action.split_ratio
        shares_after = shares_before
        avg_cost_after = avg_cost_before
        fractional_shares = 0.0
        fractional_cash = 0.0
        if abs(split_ratio - 1.0) > 0.000000001:
            exact_after = shares_before * split_ratio
            shares_after = floor(exact_after)
            fractional_shares = max(0.0, exact_after - shares_after)
            fractional_price = self._corporate_action_fractional_price(action, position)
            fractional_cash = fractional_shares * fractional_price
            if shares_after > 0:
                avg_cost_after = avg_cost_before * shares_before / shares_after
                state.positions[action.code] = EODHDPosition(
                    quantity=shares_after,
                    avg_cost=round(avg_cost_after, 4),
                )
            else:
                avg_cost_after = 0.0
                del state.positions[action.code]

        cash_gross = shares_before * action.cash_dividend_per_share
        cash_net = cash_gross
        state.cash += cash_net + fractional_cash

        record = EODHDCorporateActionRecord(
            agent_id=state.agent_id,
            code=action.code,
            exchange=action.exchange,
            ex_date=action.ex_date,
            scheme=self._corporate_action_scheme(action),
            split_ratio=round(split_ratio, 10),
            cash_dividend_per_share=round(action.cash_dividend_per_share, 10),
            dividend_currency=action.dividend_currency,
            shares_before=shares_before,
            shares_after=shares_after,
            share_delta=shares_after - shares_before,
            avg_cost_before=round(avg_cost_before, 6),
            avg_cost_after=round(avg_cost_after, 6),
            cash_dividend_gross=round(cash_gross, 2),
            cash_dividend_net=round(cash_net, 2),
            fractional_shares=round(fractional_shares, 8),
            fractional_cash=round(fractional_cash, 2),
            applied_at=timestamp,
        )
        state.corporate_actions.append(record)
        logger.info(
            "Applied EODHD corporate action: agent=%s code=%s ex=%s split=%.8g "
            "dividend=%.6f shares=%d->%d cash=%.2f fractional_cash=%.2f "
            "avg_cost=%.4f->%.4f",
            state.agent_id,
            action.code,
            action.ex_date,
            split_ratio,
            action.cash_dividend_per_share,
            shares_before,
            shares_after,
            cash_net + fractional_cash,
            fractional_cash,
            avg_cost_before,
            avg_cost_after,
        )

    def _corporate_action_fractional_price(
        self, action: EODHDCorporateAction, position: EODHDPosition
    ) -> float:
        if action.split_ratio <= 0.0:
            return 0.0
        latest_price = self._latest_prices.get(action.code)
        if latest_price is not None and latest_price > 0.0:
            return latest_price / action.split_ratio
        return position.avg_cost / action.split_ratio

    @staticmethod
    def _corporate_action_scheme(action: EODHDCorporateAction) -> str:
        parts: list[str] = []
        if abs(action.split_ratio - 1.0) > 0.000000001:
            split_label = action.split_text or f"{action.split_ratio:.8g}"
            parts.append(f"split {split_label}")
        if action.cash_dividend_per_share > 0.0:
            currency = f" {action.dividend_currency}" if action.dividend_currency else ""
            parts.append(f"dividend {action.cash_dividend_per_share:.6f}{currency}/share")
        return ", ".join(parts)

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

    async def run(self) -> None:
        self._subscribe_tracked_symbols()
        last_corporate_action_date: date | None = None
        while True:
            now = self._now()
            today = now.date()
            if last_corporate_action_date != today:
                last_corporate_action_date = today
                try:
                    await asyncio.to_thread(self.apply_corporate_actions, today)
                except Exception:
                    logger.exception("EODHD corporate-action scan failed")
            next_midnight = datetime.combine(
                today + timedelta(days=1),
                time.min,
                tzinfo=timezone.utc,
            )
            await asyncio.sleep(max(1.0, (next_midnight - self._now()).total_seconds()))
