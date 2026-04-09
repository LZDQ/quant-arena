"""Trading simulation engine."""

from collections import defaultdict
from datetime import date, datetime

from quant_arena.schemas import OperationListResponse, PortfolioResponse, PositionView, RankingEntry, SubmitOrderRequest
from quant_arena.clock import now_shanghai
from quant_arena.config import AgentConfig, AppConfig
from quant_arena.errors import BadRequestError, ConflictError, NotFoundError, UnauthorizedError
from quant_arena.market import MarketService
from quant_arena.models import (
    AgentState,
    EquityPoint,
    FillRecord,
    OrderRecord,
    PositionLot,
    QuoteSnapshot,
)
from quant_arena.storage import StorageService


class ArenaService:
    """Application service layer."""

    def __init__(self, config: AppConfig, storage: StorageService, market: MarketService):
        self.config = config
        self.storage = storage
        self.market = market
        self._agents: dict[str, AgentConfig] = {}

    def set_agents(self, agents: dict[str, AgentConfig]) -> None:
        self._agents = dict(sorted(agents.items(), key=lambda item: item[0]))

    def list_agent_items(self) -> list[tuple[str, AgentConfig]]:
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
        self.storage.save_agent_config(agent_id, agent)
        state = self._load_or_init_agent_state(agent_id, agent)
        self.storage.save_agent_state(state)
        return agent

    def update_agent(self, agent_id: str, updates: dict) -> AgentConfig:
        current = self.get_agent(agent_id)
        replaced = current.model_copy(update={key: value for key, value in updates.items() if value is not None})
        self._agents[agent_id] = replaced
        self.storage.save_agent_config(agent_id, replaced)
        state = self._load_or_init_agent_state(agent_id, current)
        if updates.get("initial_cash") is not None and not state.orders and not state.fills:
            state.cash = replaced.initial_cash
            self.storage.save_agent_state(state)
        return replaced

    def delete_agent(self, agent_id: str) -> None:
        self.get_agent(agent_id)
        del self._agents[agent_id]
        self.storage.delete_agent_dir(agent_id)

    def authenticate_agent(self, headers: dict[str, str]) -> str:
        header_value = headers.get(self.config.token_header_name.lower())
        for agent_id, agent in self._agents.items():
            if header_value == agent.token_secret:
                return agent_id
        raise UnauthorizedError("Invalid agent token")

    def submit_order(
        self,
        agent_id: str,
        request: SubmitOrderRequest,
        submitted_at: datetime | None = None
    ) -> OrderRecord:
        agent = self.get_agent(agent_id)
        now = submitted_at or now_shanghai()
        if request.side == "buy" and request.quantity % 100 != 0:
            raise BadRequestError("Buy order quantity must be a multiple of 100")
        quote = self.market.get_latest_quote(request.code)
        state = self._load_or_init_agent_state(agent_id, agent)
        order = OrderRecord(
            agent_id=agent_id,
            code=request.code,
            side=request.side,
            quantity=request.quantity,
            limit_price=request.limit_price,
            submitted_at=now,
            activate_after=quote.as_of,
        )
        state.orders.append(order)
        self.storage.save_agent_state(state)
        return order

    def cancel_order(self, agent_id: str, order_id: str) -> OrderRecord:
        agent = self.get_agent(agent_id)
        state = self._load_or_init_agent_state(agent_id, agent)
        for order in state.orders:
            if order.order_id == order_id:
                if order.status != "pending":
                    raise ConflictError("Only pending orders can be canceled")
                order.status = "canceled"
                order.canceled_at = now_shanghai()
                self.storage.save_agent_state(state)
                return order
        raise NotFoundError(f"Unknown order: {order_id}")

    def match_pending_orders(self, now: datetime | None = None) -> None:
        timestamp = now or now_shanghai()
        for agent_id, agent in self.list_agent_items():
            state = self._load_or_init_agent_state(agent_id, agent)
            pending_codes = [order.code for order in state.orders if order.status == "pending"]
            if not pending_codes:
                self._update_equity_snapshot(agent, state)
                continue
            quotes = self.market.refresh_quotes(pending_codes)
            changed = False
            for order in state.orders:
                if order.status != "pending":
                    continue
                quote = quotes.get(order.code)
                if quote is None:
                    continue
                order.last_checked_at = timestamp
                if quote.as_of <= order.activate_after:
                    continue
                if not self._crosses(order.side, order.limit_price, quote.last_price):
                    continue
                if order.side == "buy" and quote.last_price >= quote.limit_up:
                    continue
                if order.side == "sell" and quote.last_price <= quote.limit_down:
                    continue
                if not self._can_fill(agent, state, order, quote):
                    continue
                self._fill_order(state, order, quote)
                changed = True
            self._update_equity_snapshot(agent, state)
            if changed:
                self.storage.save_agent_state(state)
            else:
                self.storage.save_agent_state(state)

    def get_portfolio(self, agent_id: str) -> PortfolioResponse:
        agent = self.get_agent(agent_id)
        state = self._load_or_init_agent_state(agent_id, agent)
        return self._build_portfolio(agent, state)

    def list_operations(
        self,
        agent_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> OperationListResponse:
        agent = self.get_agent(agent_id)
        state = self._load_or_init_agent_state(agent_id, agent)
        orders = [order for order in state.orders if self._in_range(order.submitted_at, start, end)]
        fills = [fill for fill in state.fills if self._in_range(fill.executed_at, start, end)]
        if limit is not None:
            orders = orders[-limit:]
            fills = fills[-limit:]
        return OperationListResponse(orders=orders, fills=fills)

    def get_equity_curve(self, agent_id: str, start: date | None = None, end: date | None = None) -> list[EquityPoint]:
        agent = self.get_agent(agent_id)
        state = self._load_or_init_agent_state(agent_id, agent)
        points = state.equity_history
        if start is not None:
            points = [point for point in points if point.trade_date >= start]
        if end is not None:
            points = [point for point in points if point.trade_date <= end]
        return points

    def get_rankings(self, target_date: date | None = None) -> list[RankingEntry]:
        entries: list[RankingEntry] = []
        for agent_id, agent in self.list_agent_items():
            state = self._load_or_init_agent_state(agent_id, agent)
            portfolio = self._build_portfolio(agent, state)
            point = self._resolve_equity_point(state, target_date, portfolio)
            return_pct = 0.0 if agent.initial_cash == 0 else ((point.total_equity - agent.initial_cash) / agent.initial_cash) * 100.0
            entries.append(
                RankingEntry(
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

    def _resolve_equity_point(self, state: AgentState, target_date: date | None, portfolio: PortfolioResponse) -> EquityPoint:
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

    @staticmethod
    def _crosses(side: str, limit_price: float, market_price: float) -> bool:
        if side == "buy":
            return market_price <= limit_price
        return market_price >= limit_price

    def _can_fill(self, agent: AgentConfig, state: AgentState, order: OrderRecord, quote: QuoteSnapshot) -> bool:
        notional = quote.last_price * order.quantity
        commission = self._commission(notional)
        if order.side == "buy":
            return state.cash >= notional + commission
        sellable = self._sellable_quantity(state, order.code, quote.trade_date)
        return sellable >= order.quantity

    def _fill_order(self, state: AgentState, order: OrderRecord, quote: QuoteSnapshot) -> None:
        notional = quote.last_price * order.quantity
        commission = self._commission(notional)
        stamp_tax = self._stamp_tax(notional, order.side)
        fill = FillRecord(
            order_id=order.order_id,
            agent_id=order.agent_id,
            code=order.code,
            side=order.side,
            quantity=order.quantity,
            executed_at=quote.as_of,
            executed_price=quote.last_price,
            commission=commission,
            stamp_tax=stamp_tax,
        )
        if order.side == "buy":
            state.cash -= notional + commission
            state.positions.setdefault(order.code, []).append(
                PositionLot(quantity=order.quantity, acquired_date=quote.trade_date, cost_price=quote.last_price)
            )
        else:
            state.cash += notional - commission - stamp_tax
            consumed_cost = self._consume_sell_lots(state, order.code, order.quantity, quote.trade_date)
            state.realized_pnl += (quote.last_price * order.quantity) - consumed_cost - commission - stamp_tax
        order.status = "filled"
        order.filled_at = quote.as_of
        state.fills.append(fill)

    def _update_equity_snapshot(self, agent: AgentConfig, state: AgentState) -> None:
        portfolio = self._build_portfolio(agent, state)
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

    def _build_portfolio(self, agent: AgentConfig, state: AgentState) -> PortfolioResponse:
        quotes = self.market.refresh_quotes(list(state.positions.keys())) if state.positions else {}
        positions: list[PositionView] = []
        market_value = 0.0
        unrealized_pnl = 0.0
        as_of: datetime | None = None
        for code, lots in sorted(state.positions.items()):
            live_lots = [lot for lot in lots if lot.quantity > 0]
            if not live_lots:
                continue
            quantity = sum(lot.quantity for lot in live_lots)
            sellable = self._sellable_quantity(state, code, now_shanghai().date())
            avg_cost = sum(lot.quantity * lot.cost_price for lot in live_lots) / quantity
            quote = quotes.get(code)
            market_price = quote.last_price if quote is not None else None
            position_value = (market_price or 0.0) * quantity
            position_unrealized = ((market_price or avg_cost) - avg_cost) * quantity
            market_value += position_value
            unrealized_pnl += position_unrealized
            if quote is not None:
                as_of = quote.as_of if as_of is None else max(as_of, quote.as_of)
            positions.append(
                PositionView(
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
        return PortfolioResponse(
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
        fees = self.config.fees
        return round(max(fees.min_commission, notional * fees.commission_bps / 10000.0), 2)

    def _stamp_tax(self, notional: float, side: str) -> float:
        if side != "sell":
            return 0.0
        return round(notional * self.config.fees.stamp_tax_bps / 10000.0, 2)

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
        state = self.storage.load_agent_state(agent_id)
        if state is not None:
            return state
        return self._default_agent_state(agent_id, agent.initial_cash)
