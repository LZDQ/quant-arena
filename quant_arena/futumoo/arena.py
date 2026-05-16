"""Futumoo paper-trading arena (single-currency per agent).

Inherits agent registry, persistence, daily reports and rankings from
`BaseArenaService`. The Futumoo-specific parts are:

* Each agent has a single trading currency (`HKD` or `USD`) chosen at
  registration. The arena routes its orders to either the HK or US
  region accordingly; orders for codes that don't match the agent's
  region are rejected at submission.
* Pending-order matching against `last_price` polled from Futu OpenD,
  not instant fill at the limit price.
* HK board-lot enforcement and US Pattern-Day-Trader enforcement at
  submission time, so invalid orders never reach the pending list.
* No persisted daily equity history — the polling loop only refreshes
  today's portfolio prices.
"""

import asyncio
from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path

from quant_arena.arena_base import BaseArenaService
from quant_arena.config import AgentConfig, FutumooConfig
from quant_arena.errors import BadRequestError
from quant_arena.futumoo.models import FutumooAgentState
from quant_arena.futumoo.region import HKRegionArena, RegionArena, USRegionArena
from quant_arena.futumoo.service import FutumooService
from quant_arena.models import (
    OrderRecord,
    PortfolioSnapshot,
    PositionSnapshot,
    SubmitOrder,
)
from quant_arena.notifier import NotifierService

logger = getLogger(__name__)


class FutumooArenaService(BaseArenaService[FutumooAgentState]):
    """HK / US paper-trading orchestrator with one currency per agent."""

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
            state_cls=FutumooAgentState,
        )
        self.market = market
        self.config = config
        self.hk = HKRegionArena(market=market, fees=config.hk_fees)
        self.us = USRegionArena(market=market, fees=config.us_fees, config=config)
        self._region_by_currency: dict[str, RegionArena] = {
            self.hk.currency: self.hk,
            self.us.currency: self.us,
        }
        self.regions: tuple[RegionArena, ...] = (self.hk, self.us)
        self._latest_prices: dict[str, float] = {}
        self._code_names: dict[str, str] = {}
        self._snapshot_as_of: datetime | None = None

    def _absorb_snapshot_names(self, snapshots: dict[str, dict]) -> None:
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

    # ----- agent registry -----

    def add_agent(self, agent_id: str, agent: AgentConfig) -> AgentConfig:
        if agent.currency not in self._region_by_currency:
            raise BadRequestError(
                f"Futumoo agents must use HKD or USD; got {agent.currency!r}."
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
                f"`{region.code_prefix()}<symbol>` codes; got {request.code!r}."
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
                activate_after=now,
            )
            state.orders.append(order)
            self._save_agent_state(state)
            self._latest_prices[request.code] = float(snapshot_row["last_price"])
        self.notifier.notify_order_submitted(agent, order)
        self._rankings_cache = None
        return order

    # ----- matching loop -----

    def match_pending_orders(self, region: RegionArena) -> None:
        """Snapshot once for `region` and try to fill its pending orders.

        Walks every agent whose currency maps to `region` (HKD agents for
        HK, USD agents for US). Skips outside-of-session ticks.
        """
        if not region.is_trading_day(region.now().date()):
            return
        if not region.in_session(region.now()):
            return
        codes: set[str] = set()
        relevant_agents: list[str] = []
        for agent_id, agent in self.list_agents():
            if self._region_by_currency.get(agent.currency) is not region:
                continue
            relevant_agents.append(agent_id)
            state = self._state(agent_id)
            for order in state.orders:
                if order.status == "pending" and region.owns_code(order.code):
                    codes.add(order.code)
            for code in state.positions.keys():
                codes.add(code)
        if not codes:
            return
        try:
            snapshots = self.market.get_snapshots(sorted(codes))
        except Exception:
            logger.exception("Snapshot fetch failed for region %s", region.region)
            return
        if not snapshots:
            return
        self._absorb_snapshot_names(snapshots)
        max_update: datetime | None = None
        for code, row in snapshots.items():
            self._latest_prices[code] = float(row["last_price"])
            update_at = self._parse_update_time(row.get("update_time"), region)
            if update_at is not None and (max_update is None or update_at > max_update):
                max_update = update_at
        if max_update is not None:
            self._snapshot_as_of = max_update.astimezone(timezone.utc)
        with self._order_lock:
            for agent_id in relevant_agents:
                state = self._state(agent_id)
                if not any(
                    order.status == "pending" and region.owns_code(order.code)
                    for order in state.orders
                ):
                    continue
                changed = False
                for order in state.orders:
                    if order.status != "pending" or not region.owns_code(order.code):
                        continue
                    row = snapshots.get(order.code)
                    if row is None:
                        continue
                    update_at = self._parse_update_time(row.get("update_time"), region)
                    activate_after_local = order.activate_after.astimezone(region.tz)
                    if update_at is None or update_at <= activate_after_local:
                        continue
                    last_price = float(row["last_price"])
                    if order.side == "buy" and last_price > order.limit_price:
                        continue
                    if order.side == "sell" and last_price < order.limit_price:
                        continue
                    if not self._can_still_fill(region, state, order, last_price):
                        continue
                    fill = region.fill_pending(
                        state, order, last_price, update_at.astimezone(timezone.utc)
                    )
                    self.notifier.notify_order_filled(
                        self.get_agent(agent_id), order, fill
                    )
                    changed = True
                if changed:
                    self._save_agent_state(state)
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
    def _parse_update_time(raw, region: RegionArena) -> datetime | None:
        if raw is None:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("/", "-")).replace(
                tzinfo=region.tz
            )
        except ValueError:
            return None

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

    # ----- daily lifecycle -----

    def refresh_portfolio_prices(self) -> None:
        held: set[str] = set()
        for agent_id, _ in self.list_agents():
            state = self._state(agent_id)
            held.update(state.positions.keys())
        if held:
            try:
                snapshots = self.market.get_snapshots(sorted(held))
            except Exception:
                logger.exception("Portfolio snapshot refresh failed")
                snapshots = {}
            self._absorb_snapshot_names(snapshots)
            for code, row in snapshots.items():
                self._latest_prices[code] = float(row["last_price"])
        with self._order_lock:
            for agent_id, _ in self.list_agents():
                self._update_today_equity_point(self._state(agent_id))

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

    async def run(self, polling_interval_seconds: int) -> None:
        last_session_active: dict[str, bool] = {region.region: False for region in self.regions}
        while True:
            for region in self.regions:
                try:
                    in_session_now = region.in_session(region.now()) and region.is_trading_day(
                        region.now().date()
                    )
                except Exception:
                    logger.exception("Session check failed for region %s", region.region)
                    in_session_now = False
                if in_session_now:
                    try:
                        await asyncio.to_thread(self.match_pending_orders, region)
                    except Exception:
                        logger.exception("Match cycle failed for region %s", region.region)
                elif last_session_active[region.region]:
                    try:
                        await asyncio.to_thread(self.expire_overnight_orders, region)
                    except Exception:
                        logger.exception("Order expiration failed for region %s", region.region)
                last_session_active[region.region] = in_session_now
            try:
                await asyncio.to_thread(self.refresh_portfolio_prices)
            except Exception:
                logger.exception("Portfolio price refresh failed")
            await asyncio.sleep(polling_interval_seconds)
