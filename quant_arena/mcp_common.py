"""Shared MCP scaffolding used by per-broker arena MCP servers.

Both the A-share and Futumoo MCP servers expose the same authenticated
tool surface (get_portfolio, list_operations, get_self_metadata,
submit_operation, cancel_operation, daily reports, rankings). The only
per-broker variations are the `ContextVar` instance and the description
strings on `submit_operation`. This module factors out everything that
can be shared. Token authentication lives separately in
:mod:`quant_arena.mcp_auth`, which all MCP services share.
"""

from contextvars import ContextVar
from datetime import date, datetime, tzinfo
from typing import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from quant_arena.arena_base import BaseArenaService
from quant_arena.errors import BadRequestError
from quant_arena.models import (
    AgentMetadata,
    DailyReport,
    MonitoredAgentSnapshot,
    OperationLog,
    OrderRecord,
    PortfolioSnapshot,
    SpecialEvent,
    SubmitOrder,
)


def make_arena_mcp_server(
    name: str,
    get_arena: Callable[[], BaseArenaService],
    current_agent_id: ContextVar[str | None],
    submit_operation_description: str,
    fallback_tz: tzinfo,
) -> FastMCP:
    """Build an authenticated MCP server bound to one arena.

    Args:
        name: FastMCP server name, e.g. ``"quant-arena-ashare"``.
        get_arena: Callable returning the live arena instance.
        current_agent_id: ContextVar that the auth wrapper populates with
            the resolved agent id for the request.
        submit_operation_description: Per-broker description string for the
            ``submit_operation`` tool — surfaced to the client and so worth
            tailoring (T+1 / 100-lot vs offline / fill-on-submit, etc.).
        fallback_tz: Timezone applied to naive ISO 8601 datetime filter
            arguments (e.g. Shanghai for A-share, UTC for Futumoo).
    """

    def _get_current_agent_id() -> str:
        agent_id = current_agent_id.get()
        if not agent_id:
            raise RuntimeError("No authenticated agent in MCP request context")
        return agent_id

    def _parse_filter_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=fallback_tz)
        return moment

    def _require_monitor_agent() -> str:
        agent_id = _get_current_agent_id()
        agent = get_arena().get_agent(agent_id)
        if agent.role != "monitor":
            raise BadRequestError("This tool is only available for monitor agents.")
        return agent_id

    mcp = FastMCP(
        name,
        json_response=True,
        stateless_http=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        ),
    )

    @mcp.tool()
    def get_portfolio() -> PortfolioSnapshot:
        """Get current portfolio including pending orders."""

        return get_arena().get_portfolio(_get_current_agent_id())

    @mcp.tool()
    def list_operations(
        agent_id: str | None = None,
        limit: int = 10,
        start: str | None = None,
        end: str | None = None,
    ) -> OperationLog:
        """List orders and fills.

        Normal agents can only inspect themselves. `agent_id` is silently
        ignored if supplied.
        `start` and `end` are optional ISO 8601 datetime filters applied to
        order submit time and fill execution time. `limit` defaults to the
        last 10 matching orders and fills.
        """

        current = _get_current_agent_id()
        agent = get_arena().get_agent(current)
        if agent.role == "normal":
            target = current
        else:
            target = agent_id or current
        return get_arena().list_operations(
            target,
            start=_parse_filter_datetime(start),
            end=_parse_filter_datetime(end),
            limit=limit,
        )

    @mcp.tool()
    def list_special_events(
        agent_id: str | None = None,
        limit: int = 20,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[SpecialEvent]:
        """List special account events such as corporate actions (cash dividends, bonus / transfer shares).

        Normal agents can only inspect themselves. `start_date` / `end_date`
        are optional ISO 8601 dates (YYYY-MM-DD) filtering by event date.
        `limit` defaults to the last 20 matching events. Each event's
        `summary` is a ready-to-read description of what happened to the
        account.
        """

        current = _get_current_agent_id()
        target = agent_id or current
        if target != current:
            _require_monitor_agent()
        return get_arena().list_special_events(
            target,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
            limit=limit,
        )

    @mcp.tool()
    def get_self_metadata() -> AgentMetadata:
        """Get the current agent's metadata."""

        agent_id = _get_current_agent_id()
        agent = get_arena().get_agent(agent_id)
        return AgentMetadata(
            agent_id=agent_id,
            name=agent_id,
            display_name=agent.display_name,
            role=agent.role,
            currency=agent.currency,
        )

    @mcp.tool(description=submit_operation_description)
    async def submit_operation(
        code: str, side: str, quantity: int, limit_price: float, comment: str
    ) -> OrderRecord:
        return await get_arena().submit_order(
            _get_current_agent_id(),
            SubmitOrder(
                code=code,
                side=side,
                quantity=quantity,
                limit_price=limit_price,
                comment=comment,
            ),
        )

    @mcp.tool()
    def cancel_operation(order_id: str) -> OrderRecord:
        """Cancel a pending order."""

        return get_arena().cancel_order(_get_current_agent_id(), order_id)

    @mcp.tool()
    def submit_daily_report(content: str) -> str:
        """Create or overwrite today's daily report (markdown) for the calling agent."""

        report = get_arena().submit_daily_report(_get_current_agent_id(), content)
        line_count = len(report.content.splitlines())
        char_count = len(report.content)
        return (
            f"Saved daily report for {report.trade_date.isoformat()}: "
            f"{line_count} lines, {char_count} characters."
        )

    @mcp.tool()
    def get_last_daily_report_before_today() -> DailyReport | str:
        """Return the calling agent's most recent daily report whose date is strictly before today."""

        report = get_arena().get_last_daily_report_before_today(_get_current_agent_id())
        if report is None:
            return "No previous daily report found."
        return report

    @mcp.tool()
    def get_agent_last_daily_report(agent_id: str) -> DailyReport | str:
        """Return the latest daily report for the given agent. Monitor agents only."""

        _require_monitor_agent()
        report = get_arena().get_latest_daily_report(agent_id)
        if report is None:
            return f"No daily report found for agent {agent_id}."
        return report

    @mcp.tool()
    def get_current_rankings() -> list[MonitoredAgentSnapshot]:
        """Get current rankings with portfolio snapshots. Monitor agents only."""

        _require_monitor_agent()
        arena = get_arena()
        snapshots: list[MonitoredAgentSnapshot] = []
        for ranking in arena.get_rankings():
            agent = arena.get_agent(ranking.agent_id)
            snapshots.append(
                MonitoredAgentSnapshot(
                    agent_id=ranking.agent_id,
                    name=ranking.agent_id,
                    display_name=agent.display_name,
                    role=agent.role,
                    currency=agent.currency,
                    initial_cash=agent.initial_cash,
                    return_pct=ranking.return_pct,
                    portfolio=arena.get_portfolio(ranking.agent_id),
                )
            )
        return snapshots

    return mcp
