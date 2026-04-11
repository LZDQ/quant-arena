"""Trading simulation engine. A lot of code is deprecated and do not modify this."""

import json
import shutil
from datetime import date, datetime
from logging import getLogger
from pathlib import Path

import threading
import pandas as pd

from quant_arena.market import MarketService
from quant_arena.clock import SHANGHAI_TZ, now_shanghai
from quant_arena.config import AgentConfig, FeeConfig
from quant_arena.errors import BadRequestError, ConflictError, NotFoundError
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
    """Application service layer."""

    def __init__(
        self,
        agents_root: Path,
        market: MarketService,
        fees: FeeConfig,
    ):
        self.agents_root = agents_root
        self.market = market
        self.fees = fees
        self._latest_prices: dict[str, float] = {}
        self._latest_price_times: dict[str, datetime] = {}
        self.agents_root.mkdir(parents=True, exist_ok=True)
        self._agents = self._load_agents()
        self._order_lock = threading.RLock()

    def list_agents(self) -> list[tuple[str, AgentConfig]]:
        return list(sorted(self._agents.items(), key=lambda item: item[0]))

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
        state = self._default_agent_state(agent_id, agent.initial_cash)
        self._save_agent_state(state)
        return agent

    def delete_agent(self, agent_id: str) -> None:
        self.get_agent(agent_id)
        del self._agents[agent_id]
        shutil.rmtree(self._agent_dir(agent_id), ignore_errors=True)

    def submit_order(
        self,
        agent_id: str,
        request: SubmitOrder,
        submitted_at: datetime | None = None
    ) -> OrderRecord:
        with self._order_lock:
            agent = self.get_agent(agent_id)
            now = submitted_at or now_shanghai()
            if request.side == "buy" and request.quantity % 100 != 0:
                raise BadRequestError("Buy order quantity must be a multiple of 100")
            self._refresh_intraday_cache({request.code})
            if request.code not in self._latest_prices:
                raise NotFoundError(f"No intraday market data available for {request.code}")
            state = self._load_or_init_agent_state(agent_id, agent)
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
            return order

    def cancel_order(self, agent_id: str, order_id: str) -> OrderRecord:
        with self._order_lock:
            agent = self.get_agent(agent_id)
            state = self._load_or_init_agent_state(agent_id, agent)
            for order in state.orders:
                if order.order_id == order_id:
                    if order.status != "pending":
                        raise ConflictError("Only pending orders can be canceled")
                    order.status = "canceled"
                    order.canceled_at = now_shanghai()
                    self._save_agent_state(state)
                    return order
            raise NotFoundError(f"Unknown order: {order_id}")

    def match_pending_orders(self) -> None:
        """
        Match pending orders against intraday market data for the current trade date.

        Orders are checked only against rows returned by `_refresh_intraday_cache`.
        Matching walks intraday rows in chronological order starting from each order's
        `activate_after` timestamp. Daily price limits are derived from the latest
        persisted daily-bar frame returned by `MarketService.get_latest_daily_bar()`.

        Pending orders do not survive overnight. If an order's submission date is not
        today's Shanghai trade date, it is marked canceled with a rejection reason and
        skipped from matching.
        """
        with self._order_lock:
            timestamp = now_shanghai()
            today = timestamp.date()
            for agent_id, agent in self.list_agents():
                state = self._load_or_init_agent_state(agent_id, agent)
                tracked_codes = {
                    order.code for order in state.orders if order.status == "pending"
                } | set(state.positions.keys())
                if not tracked_codes:
                    continue
                intraday_frame = self._refresh_intraday_cache(tracked_codes, today)
                intraday_by_code = {
                    code: frame.reset_index(drop=True)
                    for code, frame in intraday_frame.groupby("code")
                } if not intraday_frame.empty else {}
                latest_daily_bars = self.market.get_latest_daily_bar()
                daily_bars_by_code: dict[str, pd.Series] = {}
                if latest_daily_bars is not None and not latest_daily_bars.empty:
                    for _, row in latest_daily_bars.iterrows():
                        code = str(row["code"])
                        if code in tracked_codes:
                            daily_bars_by_code[code] = row

                for order in state.orders:
                    if order.status != "pending":
                        continue
                    if order.submitted_at.date() != today:
                        order.status = "canceled"
                        order.canceled_at = timestamp
                        order.rejection_reason = "Order expired overnight"
                        continue

                    code_frame = intraday_by_code.get(order.code)
                    if code_frame is None or code_frame.empty:
                        order.last_checked_at = timestamp
                        continue

                    order.last_checked_at = timestamp
                    daily_bar = daily_bars_by_code.get(order.code)
                    if daily_bar is None:
                        continue
                    close_price = float(daily_bar["close"])
                    limit_up = round(close_price * 1.1, 2)
                    limit_down = round(close_price * 0.9, 2)
                    eligible_rows = code_frame.loc[code_frame["trade_time"] >= order.activate_after]
                    if eligible_rows.empty:
                        continue

                    for _, row in eligible_rows.iterrows():
                        market_price = float(row["price"])
                        if order.side == "buy":
                            if market_price >= limit_up:
                                continue
                            if order.limit_price < market_price:
                                continue
                        else:
                            if market_price <= limit_down:
                                continue
                            if order.limit_price > market_price:
                                continue

                        executed_at = row["trade_time"].to_pydatetime()
                        trade_date = executed_at.date()
                        if not self._can_fill(state, order, market_price, trade_date):
                            break
                        self._fill_order(state, order, market_price, executed_at, trade_date)
                        break

                self._update_equity_snapshot(state)
                self._save_agent_state(state)

    def get_portfolio(self, agent_id: str) -> PortfolioSnapshot:
        agent = self.get_agent(agent_id)
        state = self._load_or_init_agent_state(agent_id, agent)
        return self._build_portfolio(state)

    def list_operations(
        self,
        agent_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> OperationLog:
        agent = self.get_agent(agent_id)
        state = self._load_or_init_agent_state(agent_id, agent)
        orders = [order for order in state.orders if self._in_range(order.submitted_at, start, end)]
        fills = [fill for fill in state.fills if self._in_range(fill.executed_at, start, end)]
        if limit is not None:
            orders = orders[-limit:]
            fills = fills[-limit:]
        return OperationLog(orders=orders, fills=fills)

    def get_equity_curve(self, agent_id: str, start: date | None = None, end: date | None = None) -> list[EquityPoint]:
        agent = self.get_agent(agent_id)
        state = self._load_or_init_agent_state(agent_id, agent)
        points = state.equity_history
        if start is not None:
            points = [point for point in points if point.trade_date >= start]
        if end is not None:
            points = [point for point in points if point.trade_date <= end]
        return points

    def get_rankings(self, target_date: date | None = None) -> list[RankingSnapshot]:
        entries: list[RankingSnapshot] = []
        for agent_id, agent in self.list_agents():
            state = self._load_or_init_agent_state(agent_id, agent)
            portfolio = self._build_portfolio(state)
            point = self._resolve_equity_point(state, target_date, portfolio)
            return_pct = 0.0 if agent.initial_cash == 0 else ((point.total_equity - agent.initial_cash) / agent.initial_cash) * 100.0
            entries.append(
                RankingSnapshot(
                    trade_date=point.trade_date,
                    agent_id=agent_id,
                    display_name=agent.display_name,
                    total_equity=round(point.total_equity, 2),
                    return_pct=round(return_pct, 4),
                    realized_pnl=round(point.realized_pnl, 2),
                    unrealized_pnl=round(point.unrealized_pnl, 2),
                )
            )
        return sorted(entries, key=lambda entry: (-entry.total_equity, entry.agent_id))

    def _resolve_equity_point(self, state: AgentState, target_date: date | None, portfolio: PortfolioSnapshot) -> EquityPoint:
        if target_date is not None:
            for point in state.equity_history:
                if point.trade_date == target_date:
                    return point
            raise NotFoundError(f"No equity snapshot for {target_date.isoformat()}")
        today = now_shanghai().date()
        return EquityPoint(
            trade_date=today,
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
            state.positions.setdefault(order.code, []).append(
                PositionLot(quantity=order.quantity, acquired_date=trade_date, cost_price=market_price)
            )
        else:
            state.cash += notional - commission - stamp_tax
            consumed_cost = self._consume_sell_lots(state, order.code, order.quantity, trade_date)
            state.realized_pnl += (market_price * order.quantity) - consumed_cost - commission - stamp_tax
        order.status = "filled"
        order.filled_at = executed_at
        state.fills.append(fill)

    def _update_equity_snapshot(self, state: AgentState) -> None:
        portfolio = self._build_portfolio(state)
        today = now_shanghai().date()
        point = EquityPoint(
            trade_date=today,
            cash=portfolio.cash,
            market_value=portfolio.market_value,
            total_equity=portfolio.total_equity,
            realized_pnl=portfolio.realized_pnl,
            unrealized_pnl=portfolio.unrealized_pnl,
        )
        for index, existing in enumerate(state.equity_history):
            if existing.trade_date == today:
                state.equity_history[index] = point
                break
        else:
            state.equity_history.append(point)

    def _build_portfolio(self, state: AgentState) -> PortfolioSnapshot:
        today = now_shanghai().date()
        latest_daily_bars: pd.DataFrame | None = None

        positions: list[PositionSnapshot] = []
        market_value = 0.0
        unrealized_pnl = 0.0
        as_of: datetime | None = None
        for code, lots in sorted(state.positions.items()):
            live_lots = [lot for lot in lots if lot.quantity > 0]
            if not live_lots:
                continue
            quantity = sum(lot.quantity for lot in live_lots)
            sellable = self._sellable_quantity(state, code, today)
            avg_cost = sum(lot.quantity * lot.cost_price for lot in live_lots) / quantity
            market_price = self._latest_prices.get(code)
            if market_price is None:
                if latest_daily_bars is None:
                    latest_daily_bars = self.market.get_latest_daily_bar()
                if latest_daily_bars is not None and not latest_daily_bars.empty:
                    matched_rows = latest_daily_bars.loc[latest_daily_bars["code"].astype(str) == code, "close"]
                    if not matched_rows.empty:
                        market_price = float(matched_rows.iloc[-1])
            if market_price is None:
                logger.warning("No live or daily fallback price available for %s, using 0.0", code)
                market_price = 0.0
            position_value = (market_price or 0.0) * quantity
            position_unrealized = ((market_price or avg_cost) - avg_cost) * quantity
            market_value += position_value
            unrealized_pnl += position_unrealized
            cached_as_of = self._latest_price_times.get(code)
            if cached_as_of is not None:
                as_of = cached_as_of if as_of is None else max(as_of, cached_as_of)
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
            as_of=as_of,
        )

    def _refresh_intraday_cache(self, codes: set[str]) -> pd.DataFrame:
        """Refresh and update latest prices."""
        if not codes:
            return pd.DataFrame()

        today = now_shanghai().date()

        frame = pd.concat(
            [self.market.fetch_intraday(code, today=today) for code in codes],
            ignore_index=True
        )
        if frame.empty:
            return frame

        frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
        frame["trade_time"] = pd.to_datetime(
            today.strftime("%Y-%m-%d") + " " + frame["ticktime"]
        ).dt.tz_localize(SHANGHAI_TZ)
        frame = frame.sort_values(["code", "trade_time"]).reset_index(drop=True)

        for code, code_frame in frame.groupby("code"):
            latest_row = code_frame.iloc[-1]
            self._latest_prices[code] = float(latest_row["price"])
            self._latest_price_times[code] = latest_row["trade_time"].to_pydatetime()

        return frame

    def _sellable_quantity(self, state: AgentState, code: str, trade_date: date) -> int:
        lots = state.positions.get(code, [])
        return sum(lot.quantity for lot in lots if lot.quantity > 0 and lot.acquired_date < trade_date)

    def _consume_sell_lots(self, state: AgentState, code: str, quantity: int, trade_date: date) -> float:
        remaining = quantity
        total_cost = 0.0
        eligible = [lot for lot in state.positions.get(code, []) if lot.quantity > 0 and lot.acquired_date < trade_date]
        for lot in eligible:
            if remaining <= 0:
                break
            used = min(remaining, lot.quantity)
            lot.quantity -= used
            remaining -= used
            total_cost += used * lot.cost_price
        if remaining > 0:
            raise ConflictError("Insufficient sellable quantity for T+1")
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
    def _in_range(moment: datetime, start: datetime | None, end: datetime | None) -> bool:
        if start is not None and moment < start:
            return False
        if end is not None and moment > end:
            return False
        return True

    @staticmethod
    def _default_agent_state(agent_id: str, initial_cash: float) -> AgentState:
        return AgentState(agent_id=agent_id, cash=initial_cash)

    def _load_or_init_agent_state(self, agent_id: str, agent: AgentConfig) -> AgentState:
        path = self._state_path(agent_id)
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                return AgentState.model_validate(json.load(handle))
        state = self._default_agent_state(agent_id, agent.initial_cash)
        self._save_agent_state(state)
        return state

    def _load_agents(self) -> dict[str, AgentConfig]:
        agents: dict[str, AgentConfig] = {}
        for agent_dir in sorted(path for path in self.agents_root.iterdir() if path.is_dir()):
            config_path = agent_dir / "config.json"
            if not config_path.exists():
                continue
            with config_path.open("r", encoding="utf-8") as handle:
                agents[agent_dir.name] = AgentConfig.model_validate(json.load(handle))
        return agents

    def _save_agent_config(self, agent_id: str, agent: AgentConfig) -> None:
        self._agent_dir(agent_id).mkdir(parents=True, exist_ok=True)
        with self._config_path(agent_id).open("w", encoding="utf-8") as handle:
            json.dump(agent.model_dump(mode="json"), handle, ensure_ascii=False, indent="\t")
            handle.write("\n")

    def _save_agent_state(self, state: AgentState) -> None:
        self._agent_dir(state.agent_id).mkdir(parents=True, exist_ok=True)
        with self._state_path(state.agent_id).open("w", encoding="utf-8") as handle:
            json.dump(state.model_dump(mode="json"), handle, ensure_ascii=False, indent="\t")
            handle.write("\n")

    def _agent_dir(self, agent_id: str) -> Path:
        return self.agents_root / agent_id

    def _config_path(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "config.json"

    def _state_path(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "state.json"
