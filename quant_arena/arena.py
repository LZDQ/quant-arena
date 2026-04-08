"""Trading simulation engine."""

from collections import defaultdict
from datetime import date, datetime, time, timezone

from fastapi import HTTPException
from zoneinfo import ZoneInfo

from quant_arena.config import AgentConfig, AppConfig
from quant_arena.market import MarketDataProvider
from quant_arena.models import (
	AgentState,
	EquityPoint,
	FillRecord,
	FiveMinuteBar,
	MarketBarsResponse,
	MarketCodeStatus,
	MarketParseResponse,
	MarketStatusResponse,
	OperationListResponse,
	OrderRecord,
	PortfolioResponse,
	PositionLot,
	PositionView,
	QuoteSnapshot,
	RankingEntry,
	SubmitOrderRequest,
)
from quant_arena.storage import ArenaStorage

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class ArenaService:
	"""Application service layer."""

	def __init__(self, config: AppConfig, storage: ArenaStorage, market_data: MarketDataProvider):
		self.config = config
		self.storage = storage
		self.market_data = market_data
		self._agents: dict[str, AgentConfig] = {}

	def set_agents(self, agents: list[AgentConfig]) -> None:
		self._agents = {agent.agent_id: agent for agent in agents}

	def list_agents(self) -> list[AgentConfig]:
		return list(sorted(self._agents.values(), key=lambda agent: agent.agent_id))

	def get_agent(self, agent_id: str) -> AgentConfig:
		agent = self._agents.get(agent_id)
		if agent is None:
			raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_id}")
		return agent

	def add_agent(self, agent: AgentConfig) -> AgentConfig:
		if agent.agent_id in self._agents:
			raise HTTPException(status_code=409, detail=f"Agent already exists: {agent.agent_id}")
		self._agents[agent.agent_id] = agent
		self.storage.save_agent_config(agent)
		state = self.storage.load_agent_state(agent.agent_id, agent.initial_cash)
		self.storage.save_agent_state(state)
		return agent

	def update_agent(self, agent_id: str, updates: dict) -> AgentConfig:
		current = self.get_agent(agent_id)
		replaced = current.model_copy(update={key: value for key, value in updates.items() if value is not None})
		self._agents[agent_id] = replaced
		self.storage.save_agent_config(replaced)
		state = self.storage.load_agent_state(agent_id, current.initial_cash)
		if updates.get("initial_cash") is not None and not state.orders and not state.fills:
			state.cash = replaced.initial_cash
			self.storage.save_agent_state(state)
		return replaced

	def delete_agent(self, agent_id: str) -> None:
		self.get_agent(agent_id)
		del self._agents[agent_id]
		self.storage.delete_agent_state(agent_id)

	def authenticate_agent(self, headers: dict[str, str]) -> AgentConfig:
		for agent in self._agents.values():
			header_value = headers.get(agent.token_header_name.lower())
			if header_value == agent.token_secret:
				return agent
		raise HTTPException(status_code=401, detail="Invalid agent token")

	def refresh_quotes(self, codes: list[str]) -> dict[str, QuoteSnapshot]:
		normalized_codes = sorted(set(codes))
		if not normalized_codes:
			return {}
		return self.market_data.get_latest_quotes(normalized_codes)

	def sync_market_data(self, now: datetime | None = None) -> None:
		timestamp = now or datetime.now(timezone.utc)
		tracked_codes = self._tracked_codes()
		if not tracked_codes:
			return

		local_now = timestamp.astimezone(SHANGHAI_TZ)
		quotes = self.refresh_quotes(tracked_codes)
		trade_dates = {quote.trade_date for quote in quotes.values()}
		if local_now.date() in trade_dates and self._is_market_open(local_now):
			self.storage.save_five_minute_bars(self.market_data.get_five_minute_bars(tracked_codes, local_now.date()))
		if local_now.date() in trade_dates and self._is_after_market_close(local_now):
			self.storage.save_daily_bars(self.market_data.get_daily_bars(tracked_codes, local_now.date()))

	def parse_today_market_data_if_missing(self, now: datetime | None = None) -> MarketParseResponse:
		timestamp = now or datetime.now(timezone.utc)
		local_today = timestamp.astimezone(SHANGHAI_TZ).date()
		tracked_codes = self._tracked_codes()
		if not tracked_codes:
			return MarketParseResponse(
				trade_date=local_today,
				tracked_codes=[],
				parsed_daily_codes=[],
				parsed_five_minute_codes=[],
			)

		quotes = self.refresh_quotes(tracked_codes)
		today_codes = sorted(code for code, quote in quotes.items() if quote.trade_date == local_today)
		missing_daily_codes = [code for code in today_codes if self.storage.load_daily_bar(code, local_today) is None]
		missing_five_minute_codes = [code for code in today_codes if not self.storage.load_five_minute_bars(code, local_today)]

		if missing_daily_codes:
			self.storage.save_daily_bars(self.market_data.get_daily_bars(missing_daily_codes, local_today))
		if missing_five_minute_codes:
			self.storage.save_five_minute_bars(self.market_data.get_five_minute_bars(missing_five_minute_codes, local_today))

		return MarketParseResponse(
			trade_date=local_today,
			tracked_codes=today_codes,
			parsed_daily_codes=missing_daily_codes,
			parsed_five_minute_codes=missing_five_minute_codes,
		)

	def submit_order(self, agent_id: str, request: SubmitOrderRequest, submitted_at: datetime | None = None) -> OrderRecord:
		agent = self.get_agent(agent_id)
		now = submitted_at or datetime.now(timezone.utc)
		quotes = self.refresh_quotes([request.code])
		if request.code not in quotes:
			raise HTTPException(status_code=404, detail=f"No market quote available for {request.code}")
		state = self.storage.load_agent_state(agent.agent_id, agent.initial_cash)
		order = OrderRecord(
			agent_id=agent.agent_id,
			code=request.code,
			side=request.side,
			quantity=request.quantity,
			limit_price=request.limit_price,
			submitted_at=now,
			activate_after=quotes[request.code].as_of,
		)
		state.orders.append(order)
		self.storage.save_agent_state(state)
		return order

	def cancel_order(self, agent_id: str, order_id: str) -> OrderRecord:
		agent = self.get_agent(agent_id)
		state = self.storage.load_agent_state(agent.agent_id, agent.initial_cash)
		for order in state.orders:
			if order.order_id == order_id:
				if order.status != "pending":
					raise HTTPException(status_code=409, detail="Only pending orders can be canceled")
				order.status = "canceled"
				order.canceled_at = datetime.now(timezone.utc)
				self.storage.save_agent_state(state)
				return order
		raise HTTPException(status_code=404, detail=f"Unknown order: {order_id}")

	def match_pending_orders(self, now: datetime | None = None) -> None:
		timestamp = now or datetime.now(timezone.utc)
		for agent in self.list_agents():
			state = self.storage.load_agent_state(agent.agent_id, agent.initial_cash)
			pending_codes = [order.code for order in state.orders if order.status == "pending"]
			if not pending_codes:
				self._update_equity_snapshot(agent, state)
				continue
			quotes = self.refresh_quotes(pending_codes)
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
		state = self.storage.load_agent_state(agent.agent_id, agent.initial_cash)
		return self._build_portfolio(agent, state)

	def list_operations(
		self,
		agent_id: str,
		start: datetime | None = None,
		end: datetime | None = None,
		limit: int | None = None,
	) -> OperationListResponse:
		agent = self.get_agent(agent_id)
		state = self.storage.load_agent_state(agent.agent_id, agent.initial_cash)
		orders = [order for order in state.orders if self._in_range(order.submitted_at, start, end)]
		fills = [fill for fill in state.fills if self._in_range(fill.executed_at, start, end)]
		if limit is not None:
			orders = orders[-limit:]
			fills = fills[-limit:]
		return OperationListResponse(orders=orders, fills=fills)

	def get_equity_curve(self, agent_id: str, start: date | None = None, end: date | None = None) -> list[EquityPoint]:
		agent = self.get_agent(agent_id)
		state = self.storage.load_agent_state(agent.agent_id, agent.initial_cash)
		points = state.equity_history
		if start is not None:
			points = [point for point in points if point.date >= start]
		if end is not None:
			points = [point for point in points if point.date <= end]
		return points

	def get_rankings(self, target_date: date | None = None) -> list[RankingEntry]:
		entries: list[RankingEntry] = []
		for agent in self.list_agents():
			state = self.storage.load_agent_state(agent.agent_id, agent.initial_cash)
			portfolio = self._build_portfolio(agent, state)
			point = self._resolve_equity_point(state, target_date, portfolio)
			return_pct = 0.0 if agent.initial_cash == 0 else ((point.total_equity - agent.initial_cash) / agent.initial_cash) * 100.0
			entries.append(
				RankingEntry(
					date=point.date,
					agent_id=agent.agent_id,
					display_name=agent.display_name,
					total_equity=round(point.total_equity, 2),
					return_pct=round(return_pct, 4),
					realized_pnl=round(point.realized_pnl, 2),
					unrealized_pnl=round(point.unrealized_pnl, 2),
				)
			)
		return sorted(entries, key=lambda entry: (-entry.total_equity, entry.agent_id))

	def get_market_status(self) -> MarketStatusResponse:
		tracked_codes = self._tracked_codes()
		codes = sorted(set(tracked_codes) | set(self.storage.list_market_codes()))
		items: list[MarketCodeStatus] = []
		for code in codes:
			latest_daily_date = self.storage.latest_daily_bar_date(code)
			latest_five_minute_date = self.storage.latest_five_minute_bar_date(code)
			five_minute_bars: list[FiveMinuteBar] = []
			if latest_five_minute_date is not None:
				five_minute_bars = self.storage.load_five_minute_bars(code, latest_five_minute_date)
			items.append(
				MarketCodeStatus(
					code=code,
					latest_daily_bar_date=latest_daily_date,
					latest_five_minute_bar_date=latest_five_minute_date,
					five_minute_bar_count=len(five_minute_bars),
					last_five_minute_bar_time=five_minute_bars[-1].bar_time if five_minute_bars else None,
				)
			)
		return MarketStatusResponse(tracked_codes=tracked_codes, codes=items)

	def get_market_bars(self, code: str, trade_date: date | None = None) -> MarketBarsResponse:
		target_date = trade_date or self.storage.latest_five_minute_bar_date(code) or self.storage.latest_daily_bar_date(code)
		if target_date is None:
			raise HTTPException(status_code=404, detail=f"No market bars available for {code}")
		return MarketBarsResponse(
			code=code,
			trade_date=target_date,
			daily_bar=self.storage.load_daily_bar(code, target_date),
			five_minute_bars=self.storage.load_five_minute_bars(code, target_date),
		)

	def _resolve_equity_point(self, state: AgentState, target_date: date | None, portfolio: PortfolioResponse) -> EquityPoint:
		if target_date is not None:
			for point in state.equity_history:
				if point.date == target_date:
					return point
			raise HTTPException(status_code=404, detail=f"No equity snapshot for {target_date.isoformat()}")
		today = datetime.now(timezone.utc).date()
		return EquityPoint(
			date=today,
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

	def _tracked_codes(self) -> list[str]:
		codes: set[str] = set()
		for agent in self.list_agents():
			state = self.storage.load_agent_state(agent.agent_id, agent.initial_cash)
			codes.update(state.positions.keys())
			codes.update(order.code for order in state.orders if order.status == "pending")
		return sorted(codes)

	@staticmethod
	def _is_market_open(moment: datetime) -> bool:
		current = moment.timetz().replace(tzinfo=None)
		return (time(9, 30) <= current < time(11, 30)) or (time(13, 0) <= current < time(15, 0))

	@staticmethod
	def _is_after_market_close(moment: datetime) -> bool:
		current = moment.timetz().replace(tzinfo=None)
		return current >= time(15, 0)

	def _update_equity_snapshot(self, agent: AgentConfig, state: AgentState) -> None:
		portfolio = self._build_portfolio(agent, state)
		today = datetime.now(timezone.utc).date()
		point = EquityPoint(
			date=today,
			cash=portfolio.cash,
			market_value=portfolio.market_value,
			total_equity=portfolio.total_equity,
			realized_pnl=portfolio.realized_pnl,
			unrealized_pnl=portfolio.unrealized_pnl,
		)
		for index, existing in enumerate(state.equity_history):
			if existing.date == today:
				state.equity_history[index] = point
				break
		else:
			state.equity_history.append(point)

	def _build_portfolio(self, agent: AgentConfig, state: AgentState) -> PortfolioResponse:
		quotes = self.refresh_quotes(list(state.positions.keys())) if state.positions else {}
		positions: list[PositionView] = []
		market_value = 0.0
		unrealized_pnl = 0.0
		as_of: datetime | None = None
		for code, lots in sorted(state.positions.items()):
			live_lots = [lot for lot in lots if lot.quantity > 0]
			if not live_lots:
				continue
			quantity = sum(lot.quantity for lot in live_lots)
			sellable = self._sellable_quantity(state, code, datetime.now(timezone.utc).date())
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
			agent_id=agent.agent_id,
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
			raise HTTPException(status_code=409, detail="Insufficient sellable quantity for T+1")
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
