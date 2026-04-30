"""Trading simulation engine."""

import asyncio
import json
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from logging import getLogger
from pathlib import Path

import threading
import numpy as np
import pandas as pd

from quant_arena.ashare.service import AShareService
from quant_arena.clock import SHANGHAI_TZ, now_shanghai
from quant_arena.config import AgentConfig, AShareFeeConfig
from quant_arena.errors import BadRequestError, ConflictError, NotFoundError
from quant_arena.notifier import NotifierService
from quant_arena.models import (
    AgentState,
    EquityPoint,
    FillRecord,
    OperationLog,
    OrderRecord,
    PortfolioSnapshot,
    PositionLot,
    PositionSnapshot,
    RankingSnapshot,
    SubmitOrder,
)

logger = getLogger(__name__)


class ArenaService:
    """A-share trading simulator: agent state, order matching, ranking."""

    def __init__(
        self,
        agents_root: Path,
        market: AShareService,
        fees: AShareFeeConfig,
        notifier: NotifierService,
        intraday_fetch_workers: int = 8,
    ):
        self.agents_root = agents_root
        self.market = market
        self.fees = fees
        self.notifier = notifier
        self._latest_prices: dict[str, float] = {}
        self._intraday_as_of: datetime | None = None
        self._latest_close_index: dict[str, float] | None = None
        self._rankings_cache: list[RankingSnapshot] | None = None
        self._today_equity_points: dict[str, EquityPoint] = {}
        self.agents_root.mkdir(parents=True, exist_ok=True)
        self._agents: dict[str, AgentConfig] = {}
        self._states: dict[str, AgentState] = {}
        self._load_agents()
        self._order_lock = threading.RLock()
        self._intraday_executor = ThreadPoolExecutor(
            max_workers=intraday_fetch_workers,
            thread_name_prefix="intraday-fetch",
        )

    def list_agents(self) -> list[tuple[str, AgentConfig]]:
        return sorted(self._agents.items(), key=lambda item: item[0])

    def get_agent(self, agent_id: str) -> AgentConfig:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise NotFoundError(f"Unknown agent: {agent_id}")
        return agent

    def add_agent(self, agent_id: str, agent: AgentConfig) -> AgentConfig:
        if agent_id in self._agents:
            raise ConflictError(f"Agent already exists: {agent_id}")
        self._agents[agent_id] = agent
        self._save_agent_config(agent_id, agent)
        state = AgentState(agent_id=agent_id, cash=agent.initial_cash)
        self._save_agent_state(state)
        self._rankings_cache = None
        return agent

    def delete_agent(self, agent_id: str) -> None:
        self.get_agent(agent_id)
        del self._agents[agent_id]
        self._states.pop(agent_id, None)
        shutil.rmtree(self._agent_dir(agent_id), ignore_errors=True)
        self._rankings_cache = None

    async def submit_order(
        self,
        agent_id: str,
        request: SubmitOrder,
        submitted_at: datetime | None = None,
    ) -> OrderRecord:
        agent = self.get_agent(agent_id)
        now = submitted_at or now_shanghai()
        if request.side == "buy" and request.quantity % 100 != 0:
            raise BadRequestError("Buy order quantity must be a multiple of 100")
        if not (time(9, 30) <= now.time() <= time(15, 0)):
            raise BadRequestError("You can only submit an order between 9:30 and 15:00.")
        if not self._is_main_board(request.code):
            raise BadRequestError(
                f"Only main-board codes are supported (SH 60xxxx, SZ 000/001/002/003 xxxx). "
                f"{request.code} is on STAR / ChiNext / BJEX and is not accepted."
            )
        try:
            limit_down, limit_up, prev_close = self.market.fetch_price_limits(request.code)
        except Exception as exc:
            persisted_close = self._ensure_latest_close_index().get(request.code)
            if persisted_close is None:
                raise NotFoundError(
                    f"Could not resolve daily price limits for {request.code}: "
                    f"EM lookup failed ({exc}) and no persisted prev close available."
                ) from exc
            logger.warning(
                "stock_bid_ask_em failed for %s (%s); falling back to ±10%% of persisted close %.2f",
                request.code, exc, persisted_close,
            )
            prev_close = persisted_close
            limit_up = round(prev_close * 1.1, 2)
            limit_down = round(prev_close * 0.9, 2)
        if request.limit_price > limit_up:
            raise BadRequestError(
                f"Limit price {request.limit_price} exceeds today's limit-up "
                f"{limit_up} for {request.code} (prev close {prev_close}); "
                f"order would never fill."
            )
        if request.limit_price < limit_down:
            raise BadRequestError(
                f"Limit price {request.limit_price} is below today's limit-down "
                f"{limit_down} for {request.code} (prev close {prev_close}); "
                f"order would never fill."
            )
        with self._order_lock:
            state = self._state(agent_id)
            if request.side == "sell":
                sellable = self._sellable_quantity(state, request.code, now.date())
                pending_sell = sum(
                    pending.quantity
                    for pending in state.orders
                    if pending.status == "pending"
                    and pending.side == "sell"
                    and pending.code == request.code
                )
                available = sellable - pending_sell
                if request.quantity > available:
                    raise BadRequestError(
                        f"Sell quantity {request.quantity} exceeds T+1 sellable {available} "
                        f"(sellable={sellable}, encumbered_by_pending_sells={pending_sell})"
                    )
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
        self.notifier.notify_order_submitted(agent, order)
        return order

    def cancel_order(self, agent_id: str, order_id: str) -> OrderRecord:
        agent = self.get_agent(agent_id)
        with self._order_lock:
            state = self._state(agent_id)
            target: OrderRecord | None = None
            for order in state.orders:
                if order.order_id == order_id:
                    target = order
                    break
            if target is None:
                raise NotFoundError(f"Unknown order: {order_id}")
            if target.status != "pending":
                raise ConflictError("Only pending orders can be canceled")
            target.status = "canceled"
            target.canceled_at = now_shanghai()
            self._save_agent_state(state)
        self.notifier.notify_order_canceled(agent, target)
        return target

    def match_pending_orders(self) -> None:
        """
        Match pending orders against today's intraday data.

        Each tracked code is fetched once per cycle, even when multiple agents
        hold pending orders against it. Daily price-limit bands are checked at
        submit time via `fetch_price_limits`, so the matcher only walks the
        intraday tick stream for the order's limit price. End-of-session
        cleanup (overnight expiration, equity finalization) lives in
        `finalize_session`, not here.
        """
        timestamp = now_shanghai()
        self._latest_close_index = None  # rebuild per cycle for portfolio fallback pricing

        agent_pairs = self.list_agents()
        per_agent_codes: dict[str, set[str]] = {}
        all_tracked_codes: set[str] = set()
        for agent_id, _ in agent_pairs:
            state = self._state(agent_id)
            tracked = {
                order.code for order in state.orders if order.status == "pending"
            } | set(state.positions.keys())
            per_agent_codes[agent_id] = tracked
            all_tracked_codes |= tracked

        intraday_by_code = self._refresh_intraday_cache(all_tracked_codes)

        with self._order_lock:
            # Group pending orders by code so each code's frame is walked once.
            pending_by_code: dict[str, list[tuple[str, AgentState, OrderRecord]]] = {}
            dirty_states: set[str] = set()
            for agent_id, _ in agent_pairs:
                if not per_agent_codes[agent_id]:
                    continue
                state = self._state(agent_id)
                for order in state.orders:
                    if order.status != "pending":
                        continue
                    pending_by_code.setdefault(order.code, []).append((agent_id, state, order))

            for code, entries in pending_by_code.items():
                code_frame = intraday_by_code.get(code)
                for agent_id, state, order in entries:
                    if self._match_one_order(state, order, code_frame, timestamp):
                        dirty_states.add(agent_id)

            for agent_id, _ in agent_pairs:
                state = self._state(agent_id)
                self._update_today_equity_point(state)
                if agent_id in dirty_states:
                    self._save_agent_state(state)

        self._rankings_cache = None

    def finalize_session(self) -> None:
        """
        End-of-session cleanup, run once per trade-date after 15:00.

        For each agent: cancel any still-pending orders (today's leftovers and
        any stragglers from previous sessions that didn't get finalized), then
        freeze the in-memory `_today_equity_points` entry into
        `state.equity_history` and persist once.
        """
        timestamp = now_shanghai()
        today = timestamp.date()
        with self._order_lock:
            for agent_id, _ in self.list_agents():
                state = self._state(agent_id)
                for order in state.orders:
                    if order.status == "pending":
                        order.status = "canceled"
                        order.canceled_at = timestamp
                        order.rejection_reason = "Order expired at end of session"
                point = self._build_today_equity_point(state)
                self._today_equity_points[agent_id] = point
                replaced = False
                for index, existing in enumerate(state.equity_history):
                    if existing.trade_date == today:
                        state.equity_history[index] = point
                        replaced = True
                        break
                if not replaced:
                    state.equity_history.append(point)
                self._save_agent_state(state)
        self._rankings_cache = None

    def _match_one_order(
        self,
        state: AgentState,
        order: OrderRecord,
        code_frame: pd.DataFrame | None,
        timestamp: datetime,
    ) -> bool:
        """Try to fill `order` in place. Returns True only if a fill occurred."""
        if code_frame is None or code_frame.empty:
            return False

        prices: np.ndarray = code_frame["price"].to_numpy(dtype=np.float64, copy=False)
        times: np.ndarray = code_frame["trade_time"].to_numpy(copy=False)
        activate_np = np.datetime64(
            order.activate_after.astimezone(SHANGHAI_TZ).replace(tzinfo=None),
            "ns",
        )
        start_idx = int(np.searchsorted(times, activate_np, side="right"))
        if start_idx >= prices.shape[0]:
            return False
        window_prices = prices[start_idx:]
        if order.side == "buy":
            eligible_mask = window_prices <= order.limit_price
        else:
            eligible_mask = window_prices >= order.limit_price
        if not eligible_mask.any():
            return False
        matched_idx = start_idx + int(np.argmax(eligible_mask))
        market_price = float(prices[matched_idx])
        executed_at = pd.Timestamp(times[matched_idx]).to_pydatetime().replace(tzinfo=SHANGHAI_TZ)
        trade_date = executed_at.date()
        if not self._can_fill(state, order, market_price, trade_date):
            return False
        self._fill_order(state, order, market_price, executed_at, trade_date)
        return True

    async def run(self, polling_interval_seconds: int) -> None:
        """
        Match pending orders during 9:30 to 15:00, then finalize the session
        once after 15:00 each trade-date.

        `last_finalized_date` is a local that resets on process restart, so a
        server brought up after 15:00 will finalize the current trade-date on
        its first cycle even if it never ran the matcher that day.
        """
        last_finalized_date: date | None = None
        while True:
            now = now_shanghai()
            today = now.date()
            if time(9, 30) <= now.time() <= time(15, 0):
                try:
                    await asyncio.to_thread(self.match_pending_orders)
                except Exception:
                    logger.exception("Exception in matching pending orders")
            elif now.time() > time(15, 0) and last_finalized_date != today:
                try:
                    await asyncio.to_thread(self.finalize_session)
                    last_finalized_date = today
                except Exception:
                    logger.exception("Exception in finalizing session")
            await asyncio.sleep(polling_interval_seconds)

    def _ensure_latest_close_index(self) -> dict[str, float]:
        """
        Return (and cache) `code -> last_close` from `get_latest_daily_bar()`.

        Trusts the market service's "latest" contract — the entire frame is
        treated as a single day's closes. The cache is shared between the
        matcher's daily-limit lookup and `_build_portfolio`'s price fallback
        so position pricing is O(1) per code instead of `frame.loc[...]` per
        position.
        """
        if self._latest_close_index is not None:
            return self._latest_close_index
        frame = self.market.get_latest_daily_bar()
        index: dict[str, float] = {}
        if frame is not None and not frame.empty:
            code_arr = frame["code"].astype(str).to_numpy()
            close_arr = pd.to_numeric(frame["close"], errors="coerce").to_numpy()
            for code, close in zip(code_arr, close_arr, strict=False):
                if close == close:  # filter NaN
                    index[code] = float(close)
        self._latest_close_index = index
        return index

    def get_portfolio(self, agent_id: str) -> PortfolioSnapshot:
        self.get_agent(agent_id)
        with self._order_lock:
            return self._build_portfolio(self._state(agent_id))

    def list_operations(
        self,
        agent_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> OperationLog:
        self.get_agent(agent_id)
        with self._order_lock:
            state = self._state(agent_id)
            orders = [order for order in state.orders if self._in_range(order.submitted_at, start, end)]
            fills = [fill for fill in state.fills if self._in_range(fill.executed_at, start, end)]
            if limit is not None:
                orders = orders[-limit:]
                fills = fills[-limit:]
            return OperationLog(orders=orders, fills=fills)

    def get_equity_curve(self, agent_id: str, start: date | None = None, end: date | None = None) -> list[EquityPoint]:
        self.get_agent(agent_id)
        with self._order_lock:
            points = list(self._state(agent_id).equity_history)
            today_point = self._today_equity_points.get(agent_id)
            if today_point is not None and (not points or points[-1].trade_date != today_point.trade_date):
                points.append(today_point)
        if start is not None:
            points = [point for point in points if point.trade_date >= start]
        if end is not None:
            points = [point for point in points if point.trade_date <= end]
        return points

    def get_rankings(self, target_date: date | None = None) -> list[RankingSnapshot]:
        if target_date is None and self._rankings_cache is not None:
            return self._rankings_cache
        entries: list[RankingSnapshot] = []
        for agent_id, agent in self.list_agents():
            with self._order_lock:
                state = self._state(agent_id)
                portfolio = self._build_portfolio(state)
                point = self._resolve_equity_point(state, target_date, portfolio)
            return_pct = ((point.total_equity - agent.initial_cash) / agent.initial_cash) * 100.0
            entries.append(
                RankingSnapshot(
                    trade_date=point.trade_date,
                    agent_id=agent_id,
                    display_name=agent.display_name,
                    cash=round(portfolio.cash, 2),
                    market_value=round(portfolio.market_value, 2),
                    total_equity=round(point.total_equity, 2),
                    return_pct=round(return_pct, 4),
                    realized_pnl=round(point.realized_pnl, 2),
                    unrealized_pnl=round(point.unrealized_pnl, 2),
                )
            )
        ranked = sorted(entries, key=lambda entry: (-entry.total_equity, entry.agent_id))
        if target_date is None:
            self._rankings_cache = ranked
        return ranked

    def _resolve_equity_point(self, state: AgentState, target_date: date | None, portfolio: PortfolioSnapshot) -> EquityPoint:
        if target_date is not None:
            for point in state.equity_history:
                if point.trade_date == target_date:
                    return point
            raise NotFoundError(f"No equity snapshot for {target_date.isoformat()}")
        return EquityPoint(
            trade_date=now_shanghai().date(),
            cash=portfolio.cash,
            market_value=portfolio.market_value,
            total_equity=portfolio.total_equity,
            realized_pnl=portfolio.realized_pnl,
            unrealized_pnl=portfolio.unrealized_pnl,
        )

    def _can_fill(self, state: AgentState, order: OrderRecord, market_price: float, trade_date: date) -> bool:
        notional = market_price * order.quantity
        commission = self._commission(notional)
        if order.side == "buy":
            return state.cash >= notional + commission
        sellable = self._sellable_quantity(state, order.code, trade_date)
        return sellable >= order.quantity

    def _fill_order(self, state: AgentState, order: OrderRecord, market_price: float, executed_at: datetime, trade_date: date) -> None:
        notional = market_price * order.quantity
        commission = self._commission(notional)
        stamp_tax = self._stamp_tax(notional, order.side)
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
        if order.side == "buy":
            state.cash -= notional + commission
            effective_cost_price = (notional + commission) / order.quantity
            state.positions.setdefault(order.code, []).append(
                PositionLot(quantity=order.quantity, acquired_date=trade_date, cost_price=effective_cost_price)
            )
        else:
            state.cash += notional - commission - stamp_tax
            consumed_cost = self._consume_sell_lots(state, order.code, order.quantity, trade_date)
            state.realized_pnl += (market_price * order.quantity) - consumed_cost - commission - stamp_tax
        order.status = "filled"
        order.filled_at = executed_at
        state.fills.append(fill)
        self.notifier.notify_order_filled(self.get_agent(order.agent_id), order, fill)

    def _build_today_equity_point(self, state: AgentState) -> EquityPoint:
        portfolio = self._build_portfolio(state)
        return EquityPoint(
            trade_date=now_shanghai().date(),
            cash=portfolio.cash,
            market_value=portfolio.market_value,
            total_equity=portfolio.total_equity,
            realized_pnl=portfolio.realized_pnl,
            unrealized_pnl=portfolio.unrealized_pnl,
        )

    def _update_today_equity_point(self, state: AgentState) -> None:
        """In-memory only. Persisted to `state.equity_history` by `finalize_session`."""
        self._today_equity_points[state.agent_id] = self._build_today_equity_point(state)

    def _build_portfolio(self, state: AgentState) -> PortfolioSnapshot:
        today = now_shanghai().date()
        close_index: dict[str, float] | None = None

        positions: list[PositionSnapshot] = []
        market_value = 0.0
        unrealized_pnl = 0.0
        for code, lots in sorted(state.positions.items()):
            live_lots = [lot for lot in lots if lot.quantity > 0]
            if not live_lots:
                continue
            quantity = sum(lot.quantity for lot in live_lots)
            sellable = self._sellable_quantity(state, code, today)
            avg_cost = sum(lot.quantity * lot.cost_price for lot in live_lots) / quantity
            market_price = self._latest_prices.get(code)
            if market_price is None:
                if close_index is None:
                    close_index = self._ensure_latest_close_index()
                market_price = close_index.get(code)
            if market_price is None:
                logger.warning("No live or daily fallback price available for %s, using 0.0", code)
                market_price = 0.0
            position_value = market_price * quantity
            position_unrealized = (market_price - avg_cost) * quantity
            market_value += position_value
            unrealized_pnl += position_unrealized
            positions.append(
                PositionSnapshot(
                    code=code,
                    quantity=quantity,
                    sellable_quantity=sellable,
                    avg_cost=round(avg_cost, 4),
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
            as_of=self._intraday_as_of,
        )

    def _refresh_intraday_cache(self, codes: set[str]) -> dict[str, pd.DataFrame]:
        """
        Refresh per-code intraday frames in parallel and update `self._latest_prices`.

        Returns a `dict[code, DataFrame]` keyed by code. Each frame is sorted
        by `trade_time` ascending with tz-naive timestamps (Shanghai wall
        clock) plus numeric `price`. Per-code parallel HTTP fetches collapse
        the wall-clock cost of a polling cycle. `self._intraday_as_of` is
        updated to the maximum tick time observed in this cycle.
        """
        if not codes:
            return {}

        today = now_shanghai().date()
        date_prefix = today.strftime("%Y-%m-%d") + " "

        def _fetch_one(code: str) -> tuple[str, pd.DataFrame | None]:
            try:
                frame = self.market.fetch_intraday(code, today=today)
            except Exception:
                logger.exception("Intraday fetch failed for %s", code)
                return code, None
            return code, frame

        result: dict[str, pd.DataFrame] = {}
        max_tick: pd.Timestamp | None = None
        for code, frame in self._intraday_executor.map(_fetch_one, list(codes)):
            if frame is None or frame.empty:
                continue
            prices = pd.to_numeric(frame["price"], errors="coerce").to_numpy(dtype=np.float64)
            trade_time = pd.to_datetime(date_prefix + frame["ticktime"].astype(str), errors="coerce").to_numpy()
            order = np.argsort(trade_time, kind="stable")
            prices = prices[order]
            trade_time = trade_time[order]
            normalized = pd.DataFrame({"price": prices, "trade_time": trade_time}, copy=False)
            result[code] = normalized

            if prices.size > 0:
                self._latest_prices[code] = float(prices[-1])
                last_tick = pd.Timestamp(trade_time[-1])
                if max_tick is None or last_tick > max_tick:
                    max_tick = last_tick

        if max_tick is not None:
            self._intraday_as_of = max_tick.to_pydatetime().replace(tzinfo=SHANGHAI_TZ)

        return result

    def _sellable_quantity(self, state: AgentState, code: str, trade_date: date) -> int:
        lots = state.positions.get(code, [])
        return sum(lot.quantity for lot in lots if lot.quantity > 0 and lot.acquired_date < trade_date)

    def _consume_sell_lots(self, state: AgentState, code: str, quantity: int, trade_date: date) -> float:
        eligible = [lot for lot in state.positions.get(code, []) if lot.quantity > 0 and lot.acquired_date < trade_date]
        if sum(lot.quantity for lot in eligible) < quantity:
            raise ConflictError("Insufficient sellable quantity for T+1")
        remaining = quantity
        total_cost = 0.0
        for lot in eligible:
            if remaining <= 0:
                break
            used = min(remaining, lot.quantity)
            lot.quantity -= used
            remaining -= used
            total_cost += used * lot.cost_price
        state.positions[code] = [lot for lot in state.positions.get(code, []) if lot.quantity > 0]
        return total_cost

    def _commission(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return round(max(self.fees.min_commission, notional * self.fees.commission_bps / 10000.0), 2)

    def _stamp_tax(self, notional: float, side: str) -> float:
        if side != "sell":
            return 0.0
        return round(notional * self.fees.stamp_tax_bps / 10000.0, 2)

    @staticmethod
    def _is_main_board(code: str) -> bool:
        """Main board: SH 60xxxx (excluding STAR 688xxx) or SZ 000/001/002/003 xxxx."""
        if len(code) != 6 or not code.isdigit():
            return False
        if code.startswith("688"):
            return False
        if code.startswith("60"):
            return True
        return code[:3] in {"000", "001", "002", "003"}

    @staticmethod
    def _in_range(moment: datetime, start: datetime | None, end: datetime | None) -> bool:
        if start is not None and moment < start:
            return False
        if end is not None and moment > end:
            return False
        return True

    def _state(self, agent_id: str) -> AgentState:
        state = self._states.get(agent_id)
        if state is not None:
            return state
        agent = self.get_agent(agent_id)
        path = self._state_path(agent_id)
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                state = AgentState.model_validate(json.load(handle))
        else:
            state = AgentState(agent_id=agent_id, cash=agent.initial_cash)
            self._save_agent_state(state)
        self._states[agent_id] = state
        return state

    def _load_agents(self) -> None:
        for agent_dir in sorted(path for path in self.agents_root.iterdir() if path.is_dir()):
            config_path = agent_dir / "config.json"
            if not config_path.exists():
                continue
            with config_path.open("r", encoding="utf-8") as handle:
                self._agents[agent_dir.name] = AgentConfig.model_validate(json.load(handle))
            state_path = agent_dir / "state.json"
            if state_path.exists():
                with state_path.open("r", encoding="utf-8") as handle:
                    self._states[agent_dir.name] = AgentState.model_validate(json.load(handle))

    def _save_agent_config(self, agent_id: str, agent: AgentConfig) -> None:
        self._agent_dir(agent_id).mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self._config_path(agent_id), agent.model_dump(mode="json"))

    def _save_agent_state(self, state: AgentState) -> None:
        self._states[state.agent_id] = state
        self._agent_dir(state.agent_id).mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self._state_path(state.agent_id), state.model_dump(mode="json"))

    @staticmethod
    def _atomic_write_json(path: Path, payload: object) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent="\t")
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def _agent_dir(self, agent_id: str) -> Path:
        return self.agents_root / agent_id

    def _config_path(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "config.json"

    def _state_path(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "state.json"
