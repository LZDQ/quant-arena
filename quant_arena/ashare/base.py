"""A-share-local arena scaffolding.

This is intentionally copied into the A-share arena instead of shared with
other arenas. It owns the agent registry, persisted state, daily reports,
rankings, cancel flow, and manual position clear for A-share state.
"""

import json
import os
import shutil
import tempfile
import threading
from datetime import date, datetime
from logging import getLogger
from pathlib import Path

from quant_arena.config import AgentConfig
from quant_arena.errors import BadRequestError, ConflictError, NotFoundError
from quant_arena.models import (
    AgentState,
    DailyReport,
    DailyReportSummary,
    EquityPoint,
    ManualPositionClearRecord,
    OperationLog,
    OrderRecord,
    PortfolioSnapshot,
    RankingSnapshot,
    SpecialEvent,
)
from quant_arena.notifier import NotifierService

logger = getLogger(__name__)


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


class AShareArenaBase:
    """A-share-local arena scaffolding. See module docstring for the contract."""

    _DAILY_REPORT_MAX_BYTES = 256 * 1024

    def __init__(
        self,
        *,
        agents_root: Path,
        notifier: NotifierService,
    ):
        self.agents_root = agents_root
        self.notifier = notifier
        self._rankings_cache: list[RankingSnapshot] | None = None
        self._today_equity_points: dict[str, EquityPoint] = {}
        self.agents_root.mkdir(parents=True, exist_ok=True)
        self._agents: dict[str, AgentConfig] = {}
        self._states: dict[str, AgentState] = {}
        self._load_agents()
        self._order_lock = threading.RLock()

    # ----- subclass hooks -----

    def _now(self) -> datetime:
        """Return the timezone-aware current time used for clocks/dates."""
        raise NotImplementedError

    def _build_portfolio(self, state: AgentState) -> PortfolioSnapshot:
        """Build the live portfolio snapshot for `state`."""
        raise NotImplementedError

    def _special_events(self, state: AgentState) -> list[SpecialEvent]:
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
        agent.currency = None
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
        self._today_equity_points.pop(agent_id, None)
        shutil.rmtree(self._agent_dir(agent_id), ignore_errors=True)
        self._rankings_cache = None

    def update_notification_targets(
        self,
        agent_id: str,
        napcat: list[str],
        daily_report: list[str],
    ) -> AgentConfig:
        """Replace the agent's notification target lists and persist the change.

        `napcat` routes order notifications; `daily_report` routes the
        daily-report PDF (NapCat only). Returns the updated `AgentConfig`.
        Duplicates are removed while preserving order.
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
        agent.daily_report_notify_targets = _dedupe(daily_report)
        self._save_agent_config(agent_id, agent)
        return agent

    # ----- order-flow helpers -----

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
            target.canceled_at = self._now()
            self._save_agent_state(state)
        self.notifier.notify_order_canceled(agent, target)
        return target

    def manual_clear_positions(
        self,
        agent_id: str,
        comment: str,
        keep_unrealized_pnl: bool,
        keep_realized_pnl: bool,
    ) -> ManualPositionClearRecord:
        """Wipe all positions and adjust cash/realized PnL by the two keep flags.

        - `keep_unrealized_pnl=True`: positions are converted to cash at their
          last known price and the unrealized PnL becomes realized PnL.
        - `keep_unrealized_pnl=False`: positions are wound back to cost basis,
          so the floating PnL is discarded — neither cash nor realized PnL
          inherits the gain/loss.
        - `keep_realized_pnl=True`: existing realized PnL is preserved.
        - `keep_realized_pnl=False`: existing realized PnL is wiped and the
          same amount is subtracted from cash, so with both flags off the
          agent returns to its initial cash.

        All pending orders are canceled because the book is now empty. A
        `ManualPositionClearRecord` is appended to the state and surfaced as
        a `SpecialEvent`.
        """
        if not comment.strip():
            raise BadRequestError("Manual clear comment must not be empty.")
        self.get_agent(agent_id)
        with self._order_lock:
            state = self._state(agent_id)
            portfolio = self._build_portfolio(state)
            market_value = portfolio.market_value
            unrealized_pnl = portfolio.unrealized_pnl
            # Only codes with live holdings — never empty-list entries that a
            # buy-then-fully-sell can leave behind in `state.positions`.
            cleared_codes = sorted(
                position.code for position in portfolio.positions if position.quantity > 0
            )
            cash_before = float(state.cash)
            realized_before = float(state.realized_pnl)

            cost_basis = market_value - unrealized_pnl
            new_cash = cash_before + (market_value if keep_unrealized_pnl else cost_basis)
            if not keep_realized_pnl:
                new_cash -= realized_before
            new_realized = 0.0
            if keep_realized_pnl:
                new_realized += realized_before
            if keep_unrealized_pnl:
                new_realized += unrealized_pnl

            state.positions.clear()
            state.cash = round(new_cash, 4)
            state.realized_pnl = round(new_realized, 4)

            timestamp = self._now()
            for order in state.orders:
                if order.status == "pending":
                    order.status = "canceled"
                    order.canceled_at = timestamp
                    order.rejection_reason = "Canceled by manual position clear"

            record = ManualPositionClearRecord(
                agent_id=agent_id,
                applied_at=timestamp,
                comment=comment,
                keep_unrealized_pnl=keep_unrealized_pnl,
                keep_realized_pnl=keep_realized_pnl,
                cash_before=round(cash_before, 2),
                cash_after=round(state.cash, 2),
                realized_pnl_before=round(realized_before, 2),
                realized_pnl_after=round(state.realized_pnl, 2),
                market_value_before=round(market_value, 2),
                unrealized_pnl_before=round(unrealized_pnl, 2),
                cleared_codes=cleared_codes,
            )
            state.manual_position_clears.append(record)
            self._update_today_equity_point(state)
            self._save_agent_state(state)
        self._rankings_cache = None
        return record

    @staticmethod
    def _render_manual_clear_event(record: ManualPositionClearRecord) -> SpecialEvent:
        kept_unrealized = "保留" if record.keep_unrealized_pnl else "抹除"
        kept_realized = "保留" if record.keep_realized_pnl else "抹除"
        codes = "、".join(record.cleared_codes) if record.cleared_codes else "无"
        lines = [
            f"手动清仓：备注 “{record.comment}”",
            f"现金 {record.cash_before:.2f} → {record.cash_after:.2f}",
            f"已实现盈亏 {record.realized_pnl_before:.2f} → {record.realized_pnl_after:.2f}（{kept_realized}）",
            f"浮动盈亏 {record.unrealized_pnl_before:.2f}（{kept_unrealized}），清空持仓市值 {record.market_value_before:.2f}",
            f"被清空持仓：{codes}",
        ]
        return SpecialEvent(
            event_id=record.record_id,
            event_type="manual_position_clear",
            event_date=record.applied_at.date(),
            code=None,
            summary="\n".join(lines),
            occurred_at=record.applied_at,
        )

    def get_portfolio(self, agent_id: str) -> PortfolioSnapshot:
        self.get_agent(agent_id)
        with self._order_lock:
            state = self._state(agent_id)
            portfolio = self._build_portfolio(state)
            portfolio.day_return_pct = self._compute_day_return_pct(state, portfolio)
            return portfolio

    def _compute_day_return_pct(
        self, state: AgentState, portfolio: PortfolioSnapshot
    ) -> float | None:
        """Percent change vs the last `EquityPoint` whose `trade_date` is strictly
        before today. Returns None when no such point exists (day-1, fresh
        agent) or the prior equity was zero. Holidays/weekends are handled
        naturally because they leave no entry in `equity_history`. Also
        returns None when any manual position clear is dated on or after that
        baseline's trade-date — the baseline can't be trusted against a
        manually-rewritten book until the next trading-day finalize writes
        a fresh post-reset close. This covers the Saturday-reset-then-Monday
        case where the last equity_history entry is still Friday's pre-reset
        close."""
        today = self._now().date()
        prev: EquityPoint | None = None
        for point in reversed(state.equity_history):
            if point.trade_date < today:
                prev = point
                break
        if prev is None or prev.total_equity == 0:
            return None
        for record in state.manual_position_clears:
            if record.applied_at.date() >= prev.trade_date:
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
            state = self._state(agent_id)
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
            state = self._state(agent_id)
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
                    currency=None,
                    cash=round(portfolio.cash, 2),
                    market_value=round(portfolio.market_value, 2),
                    total_equity=round(point.total_equity, 2),
                    return_pct=round(return_pct, 4),
                    realized_pnl=round(point.realized_pnl, 2),
                    unrealized_pnl=round(point.unrealized_pnl, 2),
                )
            )
        # Rank by % return rather than absolute account size.
        ranked = sorted(entries, key=lambda entry: (-entry.return_pct, entry.agent_id))
        if target_date is None:
            self._rankings_cache = ranked
        return ranked

    def _resolve_equity_point(
        self,
        state: AgentState,
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

    def _build_today_equity_point(self, state: AgentState) -> EquityPoint:
        portfolio = self._build_portfolio(state)
        return EquityPoint(
            trade_date=self._now().date(),
            cash=portfolio.cash,
            market_value=portfolio.market_value,
            total_equity=portfolio.total_equity,
            realized_pnl=portfolio.realized_pnl,
            unrealized_pnl=portfolio.unrealized_pnl,
        )

    def _update_today_equity_point(self, state: AgentState) -> None:
        """In-memory only — caller is responsible for persistence."""
        self._today_equity_points[state.agent_id] = self._build_today_equity_point(state)

    def _freeze_today_equity(self, state: AgentState) -> None:
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
        agent = self.get_agent(agent_id)
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
        report = self._load_daily_report(path, today)
        self._send_daily_report_pdf(agent, agent_id, path, content)
        return report

    def _send_daily_report_pdf(
        self, agent: AgentConfig, agent_id: str, md_path: Path, content: str
    ) -> None:
        """Render the report to PDF and hand it to the notifier (best-effort).

        Delivery must never block report persistence: a missing native lib or
        a render error is logged and swallowed. The PDF file name mirrors the
        markdown file with a ``.pdf`` suffix.
        """
        try:
            from quant_arena.report_pdf import render_daily_report_pdf

            pdf_bytes = render_daily_report_pdf(content)
            file_name = md_path.with_suffix(".pdf").name
            self.notifier.notify_daily_report(agent, agent_id, file_name, pdf_bytes)
        except Exception:
            logger.exception("Failed to render/send daily-report PDF for agent %s", agent_id)

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
                agent = AgentConfig.model_validate(json.load(handle))
                agent.currency = None
                self._agents[agent_dir.name] = agent
            state_path = agent_dir / "state.json"
            if state_path.exists():
                with state_path.open("r", encoding="utf-8") as handle:
                    self._states[agent_dir.name] = AgentState.model_validate(json.load(handle))

    def _save_agent_config(self, agent_id: str, agent: AgentConfig) -> None:
        self._agent_dir(agent_id).mkdir(parents=True, exist_ok=True)
        _atomic_write_json(
            self._config_path(agent_id),
            agent.model_dump(mode="json", exclude_none=True),
        )

    def _save_agent_state(self, state: AgentState) -> None:
        self._states[state.agent_id] = state
        self._agent_dir(state.agent_id).mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._state_path(state.agent_id), state.model_dump(mode="json"))

    def _agent_dir(self, agent_id: str) -> Path:
        return self.agents_root / agent_id

    def _config_path(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "config.json"

    def _state_path(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "state.json"
