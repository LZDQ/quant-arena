"""Futumoo offline paper-trading simulator.

Mirrors the A-share `ArenaService` shape — own agent registry, own
ledger, own equity history — but with a much simpler fill model:
orders are filled instantly at the submitted limit price. There is no
order matching loop and no per-market price-band / T+1 / lot-size
gating. Symbols are passed through verbatim (`US.AAPL`, `HK.00700`,
`SH.600519`).

The Futu OpenD connection is used only for daily snapshot pricing in
`refresh_equity_snapshot` so that idle agents still see mark-to-market
changes; trading itself never touches OpenD.
"""

import asyncio
import json
import os
import shutil
import tempfile
import threading
from datetime import date, datetime, timezone
from logging import getLogger
from pathlib import Path

from quant_arena.config import AgentConfig, FutumooFeeConfig
from quant_arena.errors import BadRequestError, ConflictError, NotFoundError
from quant_arena.futumoo.models import FutumooAgentState, FutumooPosition
from quant_arena.futumoo.service import FutumooService
from quant_arena.models import (
    DailyReport,
    DailyReportSummary,
    EquityPoint,
    FillRecord,
    OperationLog,
    OrderRecord,
    PortfolioSnapshot,
    PositionSnapshot,
    RankingSnapshot,
    SubmitOrder,
)
from quant_arena.notifier import NotifierService

logger = getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class FutumooArenaService:
    """Offline paper-trading simulator across all Futu-namespaced markets."""

    def __init__(
        self,
        agents_root: Path,
        market: FutumooService,
        fees: FutumooFeeConfig,
        notifier: NotifierService,
    ):
        self.agents_root = agents_root
        self.market = market
        self.fees = fees
        self.notifier = notifier
        self._latest_prices: dict[str, float] = {}
        self._snapshot_as_of: datetime | None = None
        self._rankings_cache: list[RankingSnapshot] | None = None
        self._today_equity_points: dict[str, EquityPoint] = {}
        self.agents_root.mkdir(parents=True, exist_ok=True)
        self._agents: dict[str, AgentConfig] = {}
        self._states: dict[str, FutumooAgentState] = {}
        self._load_agents()
        self._order_lock = threading.RLock()

    # ----- agent registry -----

    def list_agents(self) -> list[tuple[str, AgentConfig]]:
        return sorted(self._agents.items(), key=lambda item: item[0])

    def get_agent(self, agent_id: str) -> AgentConfig:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise NotFoundError(f"Unknown futumoo agent: {agent_id}")
        return agent

    def add_agent(self, agent_id: str, agent: AgentConfig) -> AgentConfig:
        if agent_id in self._agents:
            raise ConflictError(f"Futumoo agent already exists: {agent_id}")
        self._agents[agent_id] = agent
        self._save_agent_config(agent_id, agent)
        state = FutumooAgentState(agent_id=agent_id, cash=agent.initial_cash)
        self._save_agent_state(state)
        self._rankings_cache = None
        return agent

    def delete_agent(self, agent_id: str) -> None:
        self.get_agent(agent_id)
        del self._agents[agent_id]
        self._states.pop(agent_id, None)
        self._today_equity_points.pop(agent_id, None)
        shutil.rmtree(self._agent_dir(agent_id), ignore_errors=True)
        self._rankings_cache = None

    # ----- order entry -----

    async def submit_order(
        self,
        agent_id: str,
        request: SubmitOrder,
        submitted_at: datetime | None = None,
    ) -> OrderRecord:
        """Record a submission and instantly fill at the limit price."""
        agent = self.get_agent(agent_id)
        now = submitted_at or _now_utc()
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

    def cancel_order(self, agent_id: str, order_id: str) -> OrderRecord:
        """Cancel a still-pending order (filled-on-submit orders cannot be canceled)."""
        agent = self.get_agent(agent_id)
        with self._order_lock:
            state = self._state(agent_id)
            target: OrderRecord | None = None
            for order in state.orders:
                if order.order_id == order_id:
                    target = order
                    break
            if target is None:
                raise NotFoundError(f"Unknown futumoo order: {order_id}")
            if target.status != "pending":
                raise ConflictError("Only pending orders can be canceled")
            target.status = "canceled"
            target.canceled_at = _now_utc()
            self._save_agent_state(state)
        self.notifier.notify_order_canceled(agent, target)
        return target

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

    # ----- portfolio / rankings -----

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

    def get_equity_curve(
        self,
        agent_id: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[EquityPoint]:
        self.get_agent(agent_id)
        with self._order_lock:
            points = list(self._state(agent_id).equity_history)
            today_point = self._today_equity_points.get(agent_id)
            if today_point is not None and (
                not points or points[-1].trade_date != today_point.trade_date
            ):
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
            return_pct = (
                (point.total_equity - agent.initial_cash) / agent.initial_cash
            ) * 100.0
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

    def _resolve_equity_point(
        self,
        state: FutumooAgentState,
        target_date: date | None,
        portfolio: PortfolioSnapshot,
    ) -> EquityPoint:
        if target_date is not None:
            for point in state.equity_history:
                if point.trade_date == target_date:
                    return point
            raise NotFoundError(f"No equity snapshot for {target_date.isoformat()}")
        return EquityPoint(
            trade_date=_now_utc().date(),
            cash=portfolio.cash,
            market_value=portfolio.market_value,
            total_equity=portfolio.total_equity,
            realized_pnl=portfolio.realized_pnl,
            unrealized_pnl=portfolio.unrealized_pnl,
        )

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
                self._snapshot_as_of = _now_utc()
        with self._order_lock:
            for agent_id, _ in self.list_agents():
                state = self._state(agent_id)
                self._today_equity_points[agent_id] = self._build_today_equity_point(state)
        self._rankings_cache = None

    def finalize_today(self) -> None:
        """Freeze each agent's today equity point into `equity_history`."""
        today = _now_utc().date()
        with self._order_lock:
            for agent_id, _ in self.list_agents():
                state = self._state(agent_id)
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

    def _build_today_equity_point(self, state: FutumooAgentState) -> EquityPoint:
        portfolio = self._build_portfolio(state)
        return EquityPoint(
            trade_date=_now_utc().date(),
            cash=portfolio.cash,
            market_value=portfolio.market_value,
            total_equity=portfolio.total_equity,
            realized_pnl=portfolio.realized_pnl,
            unrealized_pnl=portfolio.unrealized_pnl,
        )

    async def run(self, polling_interval_seconds: int) -> None:
        """Periodically refresh equity snapshots and finalize once per UTC day."""
        last_finalized: date | None = None
        while True:
            try:
                await asyncio.to_thread(self.refresh_equity_snapshot)
            except Exception:
                logger.exception("Futumoo equity refresh failed")
            today = _now_utc().date()
            if last_finalized != today:
                try:
                    await asyncio.to_thread(self.finalize_today)
                    last_finalized = today
                except Exception:
                    logger.exception("Futumoo finalize_today failed")
            await asyncio.sleep(polling_interval_seconds)

    # ----- daily reports -----

    _DAILY_REPORT_MAX_BYTES = 256 * 1024

    def submit_daily_report(self, agent_id: str, content: str) -> DailyReport:
        self.get_agent(agent_id)
        if not content.strip():
            raise BadRequestError("Daily report content must not be empty")
        if len(content.encode("utf-8")) > self._DAILY_REPORT_MAX_BYTES:
            raise BadRequestError(
                f"Daily report exceeds {self._DAILY_REPORT_MAX_BYTES} bytes"
            )
        today = _now_utc().date()
        path = self._daily_report_path(agent_id, today)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_text(path, content)
        return self._load_daily_report(path, today)

    def get_daily_report(self, agent_id: str, trade_date: date) -> DailyReport:
        self.get_agent(agent_id)
        path = self._daily_report_path(agent_id, trade_date)
        if not path.exists():
            raise NotFoundError(f"No daily report on {trade_date.isoformat()}")
        return self._load_daily_report(path, trade_date)

    def get_last_daily_report_before_today(self, agent_id: str) -> DailyReport | None:
        self.get_agent(agent_id)
        today = _now_utc().date()
        for trade_date, path in self._iter_daily_report_paths(agent_id):
            if trade_date < today:
                return self._load_daily_report(path, trade_date)
        return None

    def get_latest_daily_report(self, agent_id: str) -> DailyReport | None:
        self.get_agent(agent_id)
        for trade_date, path in self._iter_daily_report_paths(agent_id):
            return self._load_daily_report(path, trade_date)
        return None

    def list_daily_reports(
        self, agent_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[list[DailyReportSummary], int]:
        self.get_agent(agent_id)
        if page < 1:
            raise BadRequestError("page must be >= 1")
        if page_size < 1 or page_size > 100:
            raise BadRequestError("page_size must be in [1, 100]")
        entries = list(self._iter_daily_report_paths(agent_id))
        total = len(entries)
        start = (page - 1) * page_size
        end = start + page_size
        items = [
            DailyReportSummary(
                trade_date=trade_date,
                updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
            )
            for trade_date, path in entries[start:end]
        ]
        return items, total

    def _daily_reports_dir(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "daily-reports"

    def _daily_report_path(self, agent_id: str, trade_date: date) -> Path:
        return self._daily_reports_dir(agent_id) / f"{trade_date.isoformat()}.md"

    def _iter_daily_report_paths(self, agent_id: str) -> list[tuple[date, Path]]:
        directory = self._daily_reports_dir(agent_id)
        if not directory.exists():
            return []
        entries: list[tuple[date, Path]] = []
        for path in directory.iterdir():
            if path.suffix != ".md" or not path.is_file():
                continue
            try:
                trade_date = date.fromisoformat(path.stem)
            except ValueError:
                continue
            entries.append((trade_date, path))
        entries.sort(key=lambda item: item[0], reverse=True)
        return entries

    @staticmethod
    def _load_daily_report(path: Path, trade_date: date) -> DailyReport:
        return DailyReport(
            trade_date=trade_date,
            content=path.read_text(encoding="utf-8"),
            updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
        )

    # ----- persistence helpers -----

    @staticmethod
    def _in_range(moment: datetime, start: datetime | None, end: datetime | None) -> bool:
        if start is not None and moment < start:
            return False
        if end is not None and moment > end:
            return False
        return True

    def _state(self, agent_id: str) -> FutumooAgentState:
        state = self._states.get(agent_id)
        if state is not None:
            return state
        agent = self.get_agent(agent_id)
        path = self._state_path(agent_id)
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                state = FutumooAgentState.model_validate(json.load(handle))
        else:
            state = FutumooAgentState(agent_id=agent_id, cash=agent.initial_cash)
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
                    self._states[agent_dir.name] = FutumooAgentState.model_validate(
                        json.load(handle)
                    )

    def _save_agent_config(self, agent_id: str, agent: AgentConfig) -> None:
        self._agent_dir(agent_id).mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self._config_path(agent_id), agent.model_dump(mode="json"))

    def _save_agent_state(self, state: FutumooAgentState) -> None:
        self._states[state.agent_id] = state
        self._agent_dir(state.agent_id).mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self._state_path(state.agent_id), state.model_dump(mode="json"))

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

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
