"""Futumoo HK/US paper-trading arena.

Inherits agent registry, persistence, daily reports and rankings from
`BaseArenaService`. The Futumoo-specific parts are:

* Two cash buckets (HKD, USD) and two position books, owned by the
  per-region `RegionArena` strategy objects.
* Pending-order matching against `last_price` polled from Futu OpenD,
  not instant fill at the limit price.
* HK board-lot enforcement and US Pattern-Day-Trader enforcement at
  submission time, so invalid orders never reach the pending list.
* No persisted daily equity history — `equity_history` is left empty
  and the polling loop only refreshes today's portfolio prices.

`run(polling_interval_seconds)` polls each region independently: every
cycle, for every region currently in session, fetch one snapshot for the
union of (pending order codes + held position codes), update last
prices, and try to match pending orders against the new last price.
"""

import asyncio
from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path

from quant_arena.arena_base import BaseArenaService
from quant_arena.config import AgentConfig, FutumooConfig
from quant_arena.errors import BadRequestError, ConflictError
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
    """Two-region (HK + US) paper-trading orchestrator."""

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
        self.regions: tuple[RegionArena, ...] = (self.hk, self.us)
        self._latest_prices: dict[str, float] = {}
        self._snapshot_as_of: datetime | None = None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    # ----- agent registry overrides -----

    def add_agent(self, agent_id: str, agent: AgentConfig) -> AgentConfig:
        """Require both currencies on Futumoo agents and seed split cash buckets."""
        if agent.initial_cash_hkd is None or agent.initial_cash_usd is None:
            raise BadRequestError(
                "Futumoo agents require both `initial_cash_hkd` and `initial_cash_usd`."
            )
        if agent.initial_cash_hkd < 0 or agent.initial_cash_usd < 0:
            raise BadRequestError("Initial cash amounts must be non-negative.")
        if agent.initial_cash_hkd == 0 and agent.initial_cash_usd == 0:
            raise BadRequestError("At least one of initial_cash_hkd / initial_cash_usd must be positive.")
        if agent_id in self._agents:
            raise ConflictError(f"Agent already exists: {agent_id}")
        # Express the single-currency `initial_cash` as the USD-equivalent of
        # both buckets so the inherited ranking math (see BaseArenaService.get_rankings)
        # produces a sensible % return without further overrides.
        agent = agent.model_copy(
            update={
                "initial_cash": agent.initial_cash_usd
                + agent.initial_cash_hkd / self.config.fx_hkd_per_usd
            }
        )
        self._agents[agent_id] = agent
        self._save_agent_config(agent_id, agent)
        state = FutumooAgentState(
            agent_id=agent_id,
            cash=agent.initial_cash,
            cash_hkd=agent.initial_cash_hkd,
            cash_usd=agent.initial_cash_usd,
        )
        self._save_agent_state(state)
        self._rankings_cache = None
        return agent

    # ----- order entry -----

    async def submit_order(
        self,
        agent_id: str,
        request: SubmitOrder,
        submitted_at: datetime | None = None,
    ) -> OrderRecord:
        """Submit one order, validating all region rules at submission time.

        On any rule violation the order is rejected and *not* persisted as a
        canceled record — the user requested that incorrect orders never
        appear in the agent's order log.
        """
        agent = self.get_agent(agent_id)
        region = self._region_for_code(request.code)
        now = region.now()
        if submitted_at is not None:
            now = submitted_at.astimezone(region.tz)
        snapshot = self.market.get_snapshots([request.code])
        snapshot_row = snapshot.get(request.code)
        if snapshot_row is None:
            raise BadRequestError(
                f"OpenD returned no snapshot for {request.code}; cannot submit."
            )
        with self._order_lock:
            state = self._state(agent_id)
            region.validate_submission(state, request, snapshot_row, now)
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
            state.orders.append(order)
            self._save_agent_state(state)
            self._latest_prices[request.code] = float(snapshot_row["last_price"])
        self.notifier.notify_order_submitted(agent, order)
        self._rankings_cache = None
        return order

    def _region_for_code(self, code: str) -> RegionArena:
        for region in self.regions:
            if region.owns_code(code):
                return region
        raise BadRequestError(
            f"Symbol {code!r} is not supported. Use the `HK.<code>` or `US.<ticker>` form."
        )

    # ----- matching loop -----

    def match_pending_orders(self, region: RegionArena) -> None:
        """Snapshot once for `region` and try to fill its pending orders."""
        if not region.is_trading_day(region.now().date()):
            return
        if not region.in_session(region.now()):
            return
        codes: set[str] = set()
        agent_pairs = self.list_agents()
        for agent_id, _ in agent_pairs:
            state = self._state(agent_id)
            for order in state.orders:
                if order.status == "pending" and region.owns_code(order.code):
                    codes.add(order.code)
            for code in region.positions(state).keys():
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
        max_update: datetime | None = None
        for code, row in snapshots.items():
            self._latest_prices[code] = float(row["last_price"])
            update_at = self._parse_update_time(row.get("update_time"), region)
            if update_at is not None and (max_update is None or update_at > max_update):
                max_update = update_at
        if max_update is not None:
            self._snapshot_as_of = max_update.astimezone(timezone.utc)
        with self._order_lock:
            for agent_id, _ in agent_pairs:
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
        """Re-check side-specific feasibility against `market_price` before filling."""
        notional = order.quantity * market_price
        commission, stamp = region.fees_for(notional, side=order.side)
        if order.side == "buy":
            return region.cash(state) >= notional + commission + stamp
        position = region.positions(state).get(order.code)
        return position is not None and position.quantity >= order.quantity

    @staticmethod
    def _parse_update_time(raw, region: RegionArena) -> datetime | None:
        """Parse a Futu snapshot `update_time` string into a timezone-aware datetime.

        Per Futu's docs, US snapshots emit ET strings and HK snapshots emit
        Beijing time strings; both happen to coincide with `region.tz` since
        HKT and Beijing time are both UTC+8 with no DST.
        """
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
        hk_market_value = 0.0
        us_market_value = 0.0
        unrealized_pnl_hkd = 0.0
        unrealized_pnl_usd = 0.0
        for region, market_value_acc, unrealized_acc in (
            (self.hk, "hk_market_value", "unrealized_pnl_hkd"),
            (self.us, "us_market_value", "unrealized_pnl_usd"),
        ):
            for code, position in sorted(region.positions(state).items()):
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
                if region is self.hk:
                    hk_market_value += position_value
                    unrealized_pnl_hkd += position_unrealized
                else:
                    us_market_value += position_value
                    unrealized_pnl_usd += position_unrealized
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
        fx = self.config.fx_hkd_per_usd
        cash_breakdown = {
            "HKD": round(state.cash_hkd, 2),
            "USD": round(state.cash_usd, 2),
        }
        market_value_breakdown = {
            "HKD": round(hk_market_value, 2),
            "USD": round(us_market_value, 2),
        }
        cash_usd_total = state.cash_usd + state.cash_hkd / fx
        market_value_usd_total = us_market_value + hk_market_value / fx
        realized_pnl_usd_total = (
            state.realized_pnl_usd + state.realized_pnl_hkd / fx
        )
        unrealized_pnl_usd_total = (
            unrealized_pnl_usd + unrealized_pnl_hkd / fx
        )
        total_equity_usd = cash_usd_total + market_value_usd_total
        pending_orders = [order for order in state.orders if order.status == "pending"]
        return PortfolioSnapshot(
            agent_id=state.agent_id,
            cash=round(cash_usd_total, 2),
            market_value=round(market_value_usd_total, 2),
            total_equity=round(total_equity_usd, 2),
            realized_pnl=round(realized_pnl_usd_total, 2),
            unrealized_pnl=round(unrealized_pnl_usd_total, 2),
            positions=positions,
            pending_orders=pending_orders,
            as_of=self._snapshot_as_of,
            cash_breakdown=cash_breakdown,
            market_value_breakdown=market_value_breakdown,
        )

    # ----- daily lifecycle -----

    def refresh_portfolio_prices(self) -> None:
        """Pull a snapshot covering every held code (both regions) and cache last prices."""
        held_by_region: dict[RegionArena, set[str]] = {region: set() for region in self.regions}
        for agent_id, _ in self.list_agents():
            state = self._state(agent_id)
            for region in self.regions:
                held_by_region[region].update(region.positions(state).keys())
        for region, codes in held_by_region.items():
            if not codes:
                continue
            try:
                snapshots = self.market.get_snapshots(sorted(codes))
            except Exception:
                logger.exception(
                    "Portfolio snapshot refresh failed for %s", region.region
                )
                continue
            for code, row in snapshots.items():
                self._latest_prices[code] = float(row["last_price"])
        with self._order_lock:
            for agent_id, _ in self.list_agents():
                self._update_today_equity_point(self._state(agent_id))

    def expire_overnight_orders(self, region: RegionArena) -> None:
        """Cancel any pending orders that survived past `region`'s session end."""
        timestamp = region.now().astimezone(timezone.utc)
        with self._order_lock:
            for agent_id, _ in self.list_agents():
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
        """Per-region matching loop with end-of-session expiration."""
        last_session_active: dict[str, bool] = {region.region: False for region in self.regions}
        while True:
            for region in self.regions:
                try:
                    in_session_now = region.in_session(region.now()) and region.is_trading_day(
                        region.now().date()
                    )
                except Exception:
                    logger.exception(
                        "Session check failed for region %s", region.region
                    )
                    in_session_now = False
                if in_session_now:
                    try:
                        await asyncio.to_thread(self.match_pending_orders, region)
                    except Exception:
                        logger.exception(
                            "Match cycle failed for region %s", region.region
                        )
                elif last_session_active[region.region]:
                    try:
                        await asyncio.to_thread(self.expire_overnight_orders, region)
                    except Exception:
                        logger.exception(
                            "Order expiration failed for region %s", region.region
                        )
                last_session_active[region.region] = in_session_now
            try:
                await asyncio.to_thread(self.refresh_portfolio_prices)
            except Exception:
                logger.exception("Portfolio price refresh failed")
            await asyncio.sleep(polling_interval_seconds)
