"""Futumoo paper-trading arena (single-currency per agent).

Owns the HK/US/CN paper-trading runtime. The Futumoo-specific parts are:

* Each agent has a single trading currency (`HKD`, `USD`, or `CNY`) chosen at
  registration. The arena routes its orders to either the HK, US, or CN
  region accordingly; orders for codes that don't match the agent's
  region are rejected at submission.
* Event-driven pending-order matching against Futu OpenD real-time QUOTE
  pushes, with a process-wide 100-symbol LRU subscription pool.
* HK board-lot enforcement and US Pattern-Day-Trader enforcement at
  submission time, so invalid orders never reach the pending list.
* No persisted daily equity history — quote pushes only refresh today's
  in-memory portfolio prices.
"""

import asyncio
from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path

from quant_arena.arena import ArenaBase
from quant_arena.config import AgentConfig, FutumooConfig
from quant_arena.errors import BadRequestError
from quant_arena.futumoo.models import FutumooAgentState
from quant_arena.futumoo.region import CNRegionArena, HKRegionArena, RegionArena, USRegionArena
from quant_arena.futumoo.service import FutumooService
from quant_arena.models import (
    ManualPositionClearRecord,
    OrderRecord,
    PortfolioSnapshot,
    PositionSnapshot,
    SpecialEvent,
    SubmitOrder,
)
from quant_arena.notifier import NotifierService

logger = getLogger(__name__)


class FutumooArenaService(ArenaBase[FutumooAgentState]):
    """HK / US / CN paper-trading orchestrator with one currency per agent."""

    def __init__(
        self,
        agents_root: Path,
        market: FutumooService,
        config: FutumooConfig,
        notifier: NotifierService,
    ):
        super().__init__(
            agents_root=agents_root,
            notifier=notifier,
            state_type=FutumooAgentState,
        )
        self.market = market
        self.config = config
        self.hk = HKRegionArena(market=market, fees=config.hk_fees)
        self.us = USRegionArena(market=market, fees=config.us_fees, config=config)
        self.cn = CNRegionArena(market=market, fees=config.cn_fees)
        self._region_by_currency: dict[str, RegionArena] = {
            self.hk.currency: self.hk,
            self.us.currency: self.us,
            self.cn.currency: self.cn,
        }
        self.regions: tuple[RegionArena, ...] = (self.hk, self.us, self.cn)
        self._latest_prices: dict[str, float] = {}
        self._code_names: dict[str, str] = {}
        self._snapshot_as_of: datetime | None = None
        self.market.set_live_quote_handler(self._handle_live_quote)

    def _absorb_snapshot_names(
        self, snapshots: dict[str, dict[str, object]]
    ) -> None:
        """Cache `name` columns from Futu snapshots for later display."""
        for code, row in snapshots.items():
            raw = row.get("name")
            if raw is None:
                continue
            text = str(raw).strip()
            if text:
                self._code_names[code] = text

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _clear_positions(self, state: FutumooAgentState) -> None:
        state.positions.clear()

    # ----- agent registry -----

    def add_agent(self, agent_id: str, agent: AgentConfig) -> AgentConfig:
        if agent.currency not in self._region_by_currency:
            raise BadRequestError(
                f"Futumoo agents must use HKD, USD, or CNY; got {agent.currency!r}."
            )
        return super().add_agent(agent_id, agent)

    # ----- order entry -----

    async def submit_order(
        self,
        agent_id: str,
        request: SubmitOrder,
        submitted_at: datetime | None = None,
    ) -> OrderRecord:
        """Submit one order, validating all region rules at submission time.

        Invalid orders are rejected and not persisted as canceled records —
        the order log only ever shows orders that actually entered the
        pending queue.
        """
        agent = self.get_agent(agent_id)
        region = self._region_by_currency.get(agent.currency)
        if region is None:
            raise BadRequestError(
                f"Agent {agent_id} has unsupported currency {agent.currency!r}."
            )
        if not region.owns_code(request.code):
            raise BadRequestError(
                f"Agent {agent_id} trades {agent.currency} and can only submit "
                f"`{region.code_format()}` codes; got {request.code!r}."
            )
        now = region.now()
        if submitted_at is not None:
            now = submitted_at.astimezone(region.tz)
        # Snapshot fetch is sync and may stall on OpenD; offload to a worker
        # so the FastAPI event loop stays responsive (notably the toggle
        # endpoint that lets the user disable this arena).
        snapshot = await asyncio.to_thread(self.market.get_snapshots, [request.code])
        snapshot_row = snapshot.get(request.code)
        if snapshot_row is None:
            raise BadRequestError(
                f"OpenD returned no snapshot for {request.code}; cannot submit."
            )
        with self._order_lock:
            state = self._state(agent_id)
            region.validate_submission(state, request, snapshot_row, now)
            self._absorb_snapshot_names(snapshot)
            order = OrderRecord(
                agent_id=agent_id,
                code=request.code,
                name=self._code_names.get(request.code),
                side=request.side,
                quantity=request.quantity,
                limit_price=request.limit_price,
                comment=request.comment,
                submitted_at=now,
            )
            state.orders.append(order)
            self._save_agent_state(state)
            self._latest_prices[request.code] = float(snapshot_row["last_price"])
            try:
                # Keep the order lock held while registering the push so an
                # immediate first callback cannot overtake order persistence.
                self.market.subscribe_live_quotes([request.code])
            except Exception:
                state.orders.remove(order)
                self._save_agent_state(state)
                raise
            self.notifier.notify_order_submitted(agent, order)
            # Future matches are push-driven; this current validation snapshot
            # permits the newly submitted order's immediate initial match.
            self._handle_live_quote(
                request.code,
                snapshot_row,
                initial_order_id=order.order_id,
            )
        self._rankings_cache = None
        return order

    # ----- event-driven matching -----

    def _region_for_code(self, code: str) -> RegionArena | None:
        for region in self.regions:
            if region.owns_code(code):
                return region
        return None

    def _handle_live_quote(
        self,
        code: str,
        row: dict[str, object],
        initial_order_id: str | None = None,
    ) -> None:
        region = self._region_for_code(code)
        if region is None:
            return
        try:
            last_price = float(row["last_price"])
        except (KeyError, TypeError, ValueError):
            return
        if last_price <= 0:
            return
        update_at = self._parse_update_time(row.get("update_time"), region)
        if update_at is None:
            update_at = region.now()
        update_at_utc = update_at.astimezone(timezone.utc)
        self._absorb_snapshot_names({code: row})

        with self._order_lock:
            self._latest_prices[code] = last_price
            if self._snapshot_as_of is None or update_at_utc > self._snapshot_as_of:
                self._snapshot_as_of = update_at_utc
            quote_can_fill = region.in_session(update_at) and region.is_trading_day(
                update_at.date()
            )
            for agent_id, agent in self.list_agents():
                if self._region_by_currency.get(agent.currency) is not region:
                    continue
                state = self._state(agent_id)
                affects_equity = code in state.positions
                changed = False
                for order in state.orders:
                    if (
                        not quote_can_fill
                        or order.status != "pending"
                        or order.code != code
                    ):
                        continue
                    submitted_at_local = order.submitted_at.astimezone(region.tz)
                    if (
                        order.order_id != initial_order_id
                        and update_at <= submitted_at_local
                    ):
                        continue
                    if order.side == "buy" and last_price > order.limit_price:
                        continue
                    if order.side == "sell" and last_price < order.limit_price:
                        continue
                    if not self._can_still_fill(region, state, order, last_price):
                        continue
                    executed_at = update_at_utc
                    submitted_at_utc = order.submitted_at.astimezone(timezone.utc)
                    if executed_at < submitted_at_utc:
                        executed_at = submitted_at_utc
                    fill = region.fill_pending(
                        state,
                        order,
                        last_price,
                        executed_at,
                    )
                    self.notifier.notify_order_filled(
                        self.get_agent(agent_id), order, fill
                    )
                    changed = True
                if changed:
                    self._save_agent_state(state)
                if affects_equity or changed:
                    self._update_today_equity_point(state)
        self._rankings_cache = None

    def _can_still_fill(
        self,
        region: RegionArena,
        state: FutumooAgentState,
        order: OrderRecord,
        market_price: float,
    ) -> bool:
        notional = order.quantity * market_price
        commission, stamp = region.fees_for(notional, side=order.side)
        if order.side == "buy":
            return state.cash >= notional + commission + stamp
        position = state.positions.get(order.code)
        return position is not None and position.quantity >= order.quantity

    @staticmethod
    def _parse_update_time(raw: object, region: RegionArena) -> datetime | None:
        if raw is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw).replace("/", "-"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=region.tz)
        return parsed.astimezone(region.tz)

    # ----- special events -----

    def _special_events(self, state: FutumooAgentState) -> list[SpecialEvent]:
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
            f"Manual position clear · note “{record.comment}”",
            f"Cash {record.cash_before:.2f} → {record.cash_after:.2f}",
            f"Realized P&L {record.realized_pnl_before:.2f} → {record.realized_pnl_after:.2f} ({kept_realized})",
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

    def _build_portfolio(self, state: FutumooAgentState) -> PortfolioSnapshot:
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
        currency = agent.currency if agent is not None else "USD"
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

    # ----- subscription and session lifecycle -----

    def _subscribe_tracked_symbols(self) -> None:
        pending_codes: list[str] = []
        held_codes: list[str] = []
        for agent_id, _ in self.list_agents():
            state = self._state(agent_id)
            pending_codes.extend(
                order.code for order in state.orders if order.status == "pending"
            )
            held_codes.extend(state.positions.keys())
        seen: set[str] = set()
        for code in pending_codes + held_codes:
            if code in seen:
                continue
            seen.add(code)
            try:
                self.market.subscribe_live_quotes([code])
            except Exception:
                logger.exception("Failed to restore Futu quote subscription for %s", code)

    def expire_overnight_orders(self, region: RegionArena) -> None:
        timestamp = region.now().astimezone(timezone.utc)
        with self._order_lock:
            for agent_id, agent in self.list_agents():
                if self._region_by_currency.get(agent.currency) is not region:
                    continue
                state = self._state(agent_id)
                changed = False
                for order in state.orders:
                    if order.status != "pending":
                        continue
                    if not region.owns_code(order.code):
                        continue
                    order.status = "canceled"
                    order.canceled_at = timestamp
                    order.rejection_reason = (
                        f"Order expired at {region.region} session close."
                    )
                    self.notifier.notify_order_canceled(self.get_agent(agent_id), order)
                    changed = True
                if changed:
                    self._save_agent_state(state)
        self._rankings_cache = None

    async def run(self, maintenance_interval_seconds: int) -> None:
        for region in self.regions:
            try:
                await asyncio.to_thread(region.is_trading_day, region.now().date())
            except Exception:
                logger.exception(
                    "Initial session check failed for region %s",
                    region.region,
                )
        await asyncio.to_thread(self._subscribe_tracked_symbols)
        last_session_active: dict[str, bool] = {
            region.region: False for region in self.regions
        }
        while True:
            for region in self.regions:
                try:
                    now = region.now()
                    in_session_now = region.in_session(now) and region.is_trading_day(
                        now.date()
                    )
                except Exception:
                    logger.exception("Session check failed for region %s", region.region)
                    in_session_now = False
                if not in_session_now and last_session_active[region.region]:
                    try:
                        await asyncio.to_thread(self.expire_overnight_orders, region)
                    except Exception:
                        logger.exception(
                            "Order expiration failed for region %s",
                            region.region,
                        )
                last_session_active[region.region] = in_session_now
            await asyncio.sleep(max(1, maintenance_interval_seconds))
