"""Shared scaffolding for arena simulators (A-share, Futumoo, …).

Holds everything that's identical across the per-broker arenas: the
agent registry, persisted state CRUD, daily-report storage, equity
curve / rankings / operations queries, the equity-point freeze, and
the common cancel-order flow.

Subclasses must provide a `_now()` clock and a `_build_portfolio()`
implementation tailored to their position-accounting model. The
state type is passed as a constructor argument so the base can
validate / construct it from disk.
"""

import json
import os
import shutil
import tempfile
import threading
from datetime import date, datetime
from logging import getLogger
from pathlib import Path
from typing import Any, Generic, TypeVar

from quant_arena.config import AgentConfig
from quant_arena.errors import BadRequestError, ConflictError, NotFoundError
from quant_arena.models import (
    DailyReport,
    DailyReportSummary,
    EquityPoint,
    OperationLog,
    OrderRecord,
    PortfolioSnapshot,
    RankingSnapshot,
    SpecialEvent,
)
from quant_arena.notifier import NotifierService

logger = getLogger(__name__)


StateT = TypeVar("StateT")


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


class BaseArenaService(Generic[StateT]):
    """Common arena scaffolding. See module docstring for the contract."""

    _DAILY_REPORT_MAX_BYTES = 256 * 1024

    def __init__(
        self,
        *,
        agents_root: Path,
        notifier: NotifierService,
        state_cls: type,
    ):
        self.agents_root = agents_root
        self.notifier = notifier
        self._state_cls = state_cls
        self._rankings_cache: list[RankingSnapshot] | None = None
        self._today_equity_points: dict[str, EquityPoint] = {}
        self.agents_root.mkdir(parents=True, exist_ok=True)
        self._agents: dict[str, AgentConfig] = {}
        self._states: dict[str, StateT] = {}
        self._load_agents()
        self._order_lock = threading.RLock()

    # ----- subclass hooks -----

    def _now(self) -> datetime:
        """Return the timezone-aware current time used for clocks/dates."""
        raise NotImplementedError

    def _build_portfolio(self, state: StateT) -> PortfolioSnapshot:
        """Build the live portfolio snapshot for `state`."""
        raise NotImplementedError

    def _special_events(self, state: StateT) -> list[SpecialEvent]:
        """Subclass hook: non-trade account events (corporate actions, …) for `state`.

        Default is no events; arenas that model such events override this.
        """
        return []

    # ----- agent registry -----

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
        state = self._state_cls(agent_id=agent_id, cash=agent.initial_cash)
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

    def update_notification_targets(
        self,
        agent_id: str,
        napcat: list[str],
        qq_open: list[str],
    ) -> AgentConfig:
        """Replace the agent's notification target lists and persist the change.

        Returns the updated `AgentConfig`. Duplicates are removed while
        preserving order.
        """
        agent = self.get_agent(agent_id)

        def _dedupe(items: list[str]) -> list[str]:
            seen: set[str] = set()
            result: list[str] = []
            for item in items:
                if item in seen:
                    continue
                seen.add(item)
                result.append(item)
            return result

        agent.napcat_notify_targets = _dedupe(napcat)
        agent.qq_open_notify_targets = _dedupe(qq_open)
        self._save_agent_config(agent_id, agent)
        return agent

    # ----- order-flow helpers -----

    def cancel_order(self, agent_id: str, order_id: str) -> OrderRecord:
        agent = self.get_agent(agent_id)
        with self._order_lock:
            state: Any = self._state(agent_id)
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
            target.canceled_at = self._now()
            self._save_agent_state(state)
        self.notifier.notify_order_canceled(agent, target)
        return target

    def get_portfolio(self, agent_id: str) -> PortfolioSnapshot:
        self.get_agent(agent_id)
        with self._order_lock:
            state: Any = self._state(agent_id)
            portfolio = self._build_portfolio(state)
            portfolio.day_return_pct = self._compute_day_return_pct(state, portfolio)
            return portfolio

    def _compute_day_return_pct(
        self, state: Any, portfolio: PortfolioSnapshot
    ) -> float | None:
        """Percent change vs the last `EquityPoint` whose `trade_date` is strictly
        before today. Returns None when no such point exists (day-1, fresh
        agent) or the prior equity was zero. Holidays/weekends are handled
        naturally because they leave no entry in `equity_history`."""
        today = self._now().date()
        prev: EquityPoint | None = None
        for point in reversed(state.equity_history):
            if point.trade_date < today:
                prev = point
                break
        if prev is None or prev.total_equity == 0:
            return None
        return (portfolio.total_equity - prev.total_equity) / prev.total_equity * 100.0

    def list_operations(
        self,
        agent_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> OperationLog:
        self.get_agent(agent_id)
        with self._order_lock:
            state: Any = self._state(agent_id)
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
            state: Any = self._state(agent_id)
            points = list(state.equity_history)
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

    def list_special_events(
        self,
        agent_id: str,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int | None = None,
    ) -> list[SpecialEvent]:
        """Non-trade account events for `agent_id`, oldest first.

        `start_date` / `end_date` filter by `event_date` (inclusive). `limit`
        keeps only the last N matching events.
        """
        self.get_agent(agent_id)
        with self._order_lock:
            state: Any = self._state(agent_id)
            events = list(self._special_events(state))
        events.sort(key=lambda event: (event.event_date, event.occurred_at))
        if start_date is not None:
            events = [event for event in events if event.event_date >= start_date]
        if end_date is not None:
            events = [event for event in events if event.event_date <= end_date]
        if limit is not None:
            events = events[-limit:]
        return events

    def get_rankings(self, target_date: date | None = None) -> list[RankingSnapshot]:
        if target_date is None and self._rankings_cache is not None:
            return self._rankings_cache
        entries: list[RankingSnapshot] = []
        for agent_id, agent in self.list_agents():
            with self._order_lock:
                state: Any = self._state(agent_id)
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
                    currency=agent.currency,
                    cash=round(portfolio.cash, 2),
                    market_value=round(portfolio.market_value, 2),
                    total_equity=round(point.total_equity, 2),
                    return_pct=round(return_pct, 4),
                    realized_pnl=round(point.realized_pnl, 2),
                    unrealized_pnl=round(point.unrealized_pnl, 2),
                )
            )
        # Rank by % return so HKD and USD agents sit on a comparable scale.
        ranked = sorted(entries, key=lambda entry: (-entry.return_pct, entry.agent_id))
        if target_date is None:
            self._rankings_cache = ranked
        return ranked

    def _resolve_equity_point(
        self,
        state: Any,
        target_date: date | None,
        portfolio: PortfolioSnapshot,
    ) -> EquityPoint:
        if target_date is not None:
            for point in state.equity_history:
                if point.trade_date == target_date:
                    return point
            raise NotFoundError(f"No equity snapshot for {target_date.isoformat()}")
        return EquityPoint(
            trade_date=self._now().date(),
            cash=portfolio.cash,
            market_value=portfolio.market_value,
            total_equity=portfolio.total_equity,
            realized_pnl=portfolio.realized_pnl,
            unrealized_pnl=portfolio.unrealized_pnl,
        )

    def _build_today_equity_point(self, state: StateT) -> EquityPoint:
        portfolio = self._build_portfolio(state)
        return EquityPoint(
            trade_date=self._now().date(),
            cash=portfolio.cash,
            market_value=portfolio.market_value,
            total_equity=portfolio.total_equity,
            realized_pnl=portfolio.realized_pnl,
            unrealized_pnl=portfolio.unrealized_pnl,
        )

    def _update_today_equity_point(self, state: Any) -> None:
        """In-memory only — caller is responsible for persistence."""
        self._today_equity_points[state.agent_id] = self._build_today_equity_point(state)

    def _freeze_today_equity(self, state: Any) -> None:
        """Replace or append today's equity point in `state.equity_history`."""
        today = self._now().date()
        point = self._build_today_equity_point(state)
        self._today_equity_points[state.agent_id] = point
        for index, existing in enumerate(state.equity_history):
            if existing.trade_date == today:
                state.equity_history[index] = point
                return
        state.equity_history.append(point)

    # ----- daily reports -----

    def submit_daily_report(self, agent_id: str, content: str) -> DailyReport:
        self.get_agent(agent_id)
        if not content.strip():
            raise BadRequestError("Daily report content must not be empty")
        if len(content.encode("utf-8")) > self._DAILY_REPORT_MAX_BYTES:
            raise BadRequestError(
                f"Daily report exceeds {self._DAILY_REPORT_MAX_BYTES} bytes"
            )
        today = self._now().date()
        path = self._daily_report_path(agent_id, today)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, content)
        return self._load_daily_report(path, today)

    def get_daily_report(self, agent_id: str, trade_date: date) -> DailyReport:
        self.get_agent(agent_id)
        path = self._daily_report_path(agent_id, trade_date)
        if not path.exists():
            raise NotFoundError(f"No daily report on {trade_date.isoformat()}")
        return self._load_daily_report(path, trade_date)

    def get_last_daily_report_before_today(self, agent_id: str) -> DailyReport | None:
        self.get_agent(agent_id)
        today = self._now().date()
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
        tz = self._now().tzinfo
        items = [
            DailyReportSummary(
                trade_date=trade_date,
                updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=tz),
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

    def _load_daily_report(self, path: Path, trade_date: date) -> DailyReport:
        tz = self._now().tzinfo
        return DailyReport(
            trade_date=trade_date,
            content=path.read_text(encoding="utf-8"),
            updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=tz),
        )

    # ----- persistence -----

    @staticmethod
    def _in_range(moment: datetime, start: datetime | None, end: datetime | None) -> bool:
        if start is not None and moment < start:
            return False
        if end is not None and moment > end:
            return False
        return True

    def _state(self, agent_id: str) -> StateT:
        state = self._states.get(agent_id)
        if state is not None:
            return state
        agent = self.get_agent(agent_id)
        path = self._state_path(agent_id)
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                state = self._state_cls.model_validate(json.load(handle))
        else:
            state = self._state_cls(agent_id=agent_id, cash=agent.initial_cash)
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
                    self._states[agent_dir.name] = self._state_cls.model_validate(
                        json.load(handle)
                    )

    def _save_agent_config(self, agent_id: str, agent: AgentConfig) -> None:
        self._agent_dir(agent_id).mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._config_path(agent_id), agent.model_dump(mode="json"))

    def _save_agent_state(self, state: Any) -> None:
        self._states[state.agent_id] = state
        self._agent_dir(state.agent_id).mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._state_path(state.agent_id), state.model_dump(mode="json"))

    def _agent_dir(self, agent_id: str) -> Path:
        return self.agents_root / agent_id

    def _config_path(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "config.json"

    def _state_path(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "state.json"
