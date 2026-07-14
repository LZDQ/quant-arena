"""A-share MCP server integration for quant-arena."""

import asyncio
import re
from contextvars import ContextVar
from datetime import date, datetime, time, timedelta
from typing import Callable

import pandas as pd
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from quant_arena.ashare.arena import ArenaService
from quant_arena.ashare.clock import SHANGHAI_TZ
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


_CURRENT_AGENT_ID: ContextVar[str | None] = ContextVar(
    "quant_arena_current_agent_id", default=None
)
_AGENT_TOKEN_HEADER = "quant-arena-token"
_INTRADAY_INTERVAL_PATTERN = re.compile(r"^([1-9][0-9]*)(m|h)$")


class AShareIntradayBar(BaseModel):
    """One Shanghai-local OHLCV bar aggregated from Sina trades."""

    code: str
    start_at: datetime
    end_at: datetime
    local_time: str
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)
    trade_count: int = Field(gt=0)


class AShareIntradayQuotes(BaseModel):
    """Current-day intraday bars for one A-share symbol."""

    code: str
    name: str | None = None
    timezone: str = "Asia/Shanghai"
    trade_date: date
    start_time: str
    interval: str
    interval_minutes: int = Field(gt=0)
    cache_timeout_seconds: int = Field(ge=0)
    latest_price: float | None = None
    as_of: datetime | None = None
    bars: list[AShareIntradayBar]


def _request_headers(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _extract_agent_token(headers: dict[str, str]) -> str | None:
    token = headers.get(_AGENT_TOKEN_HEADER) or None
    if token is None:
        authorization = headers.get("authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization[len("Bearer "):]
    return token


def _ensure_json_accept(scope: Scope, headers: dict[str, str]) -> Scope:
    accept = headers.get("accept")
    if accept is not None and "application/json" in accept:
        return scope
    raw_headers = [
        (key, value)
        for key, value in scope.get("headers", [])
        if key.lower() != b"accept"
    ]
    raw_headers.append((b"accept", b"application/json"))
    next_scope = dict(scope)
    next_scope["headers"] = raw_headers
    return next_scope


def _current_agent_id() -> str:
    agent_id = _CURRENT_AGENT_ID.get()
    if not agent_id:
        raise RuntimeError("No authenticated agent in MCP request context")
    return agent_id


def _normalize_ashare_code(code: str) -> str:
    normalized = code.strip()
    if len(normalized) != 6 or not normalized.isdigit():
        raise BadRequestError(
            f"Expected one six-digit A-share code such as 600519; got {code!r}."
        )
    return normalized


def _parse_start_time(value: str) -> time:
    normalized = value.strip()
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(normalized, pattern).time()
        except ValueError:
            continue
    raise BadRequestError(
        f"Expected start_time in HH:MM or HH:MM:SS format; got {value!r}."
    )


def _parse_intraday_interval(value: str) -> tuple[str, int]:
    normalized = value.strip().lower()
    matched = _INTRADAY_INTERVAL_PATTERN.fullmatch(normalized)
    if matched is None:
        raise BadRequestError(
            f"Expected interval such as 5m, 15m, or 1h; got {value!r}."
        )
    amount = int(matched.group(1))
    interval_minutes = amount if matched.group(2) == "m" else amount * 60
    if interval_minutes > 1440:
        raise BadRequestError("interval must be no more than 24 hours.")
    return normalized, interval_minutes


def _aggregate_intraday_bars(
    frame: pd.DataFrame,
    code: str,
    trade_date: date,
    start: time,
    interval_minutes: int,
) -> tuple[list[AShareIntradayBar], float | None, datetime | None]:
    if frame.empty or "ticktime" not in frame.columns or "price" not in frame.columns:
        return [], None, None

    date_prefix = trade_date.strftime("%Y-%m-%d") + " "
    trade_times = pd.to_datetime(
        date_prefix + frame["ticktime"].astype(str),
        errors="coerce",
    )
    prices = pd.to_numeric(frame["price"], errors="coerce")
    if "volume" in frame.columns:
        volumes = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    else:
        volumes = pd.Series(0.0, index=frame.index)
    valid = trade_times.notna() & prices.notna() & (volumes >= 0.0)
    if not valid.any():
        return [], None, None

    normalized = pd.DataFrame(
        {
            "trade_time": trade_times[valid],
            "price": prices[valid],
            "volume": volumes[valid],
        }
    ).sort_values(by=["trade_time"], kind="stable", ignore_index=True)
    latest_time = pd.Timestamp(normalized.iloc[-1]["trade_time"])
    latest_price = float(normalized.iloc[-1]["price"])
    as_of = latest_time.to_pydatetime().replace(tzinfo=SHANGHAI_TZ)

    start_at = datetime.combine(trade_date, start)
    window = normalized.loc[normalized["trade_time"] >= start_at].copy()
    if window.empty:
        return [], latest_price, as_of
    interval_seconds = interval_minutes * 60
    bucket_indexes = (
        (window["trade_time"] - start_at).dt.total_seconds() // interval_seconds
    ).astype("int64")
    window["bar_start"] = pd.Timestamp(start_at) + pd.to_timedelta(
        bucket_indexes * interval_minutes,
        unit="m",
    )
    aggregated = window.groupby("bar_start", sort=True, as_index=False).agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("volume", "sum"),
        trade_count=("price", "size"),
    )

    bars: list[AShareIntradayBar] = []
    duration = timedelta(minutes=interval_minutes)
    for _, row in aggregated.iterrows():
        bar_start = pd.Timestamp(row["bar_start"]).to_pydatetime().replace(
            tzinfo=SHANGHAI_TZ
        )
        bars.append(
            AShareIntradayBar(
                code=code,
                start_at=bar_start,
                end_at=bar_start + duration,
                local_time=bar_start.strftime("%H:%M:%S"),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                trade_count=int(row["trade_count"]),
            )
        )
    return bars, latest_price, as_of


def create_ashare_mcp_server(get_arena: Callable[[], ArenaService]) -> FastMCP:
    """Create the A-share arena MCP server."""

    def parse_filter_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=SHANGHAI_TZ)
        return moment

    def require_monitor_agent() -> str:
        agent_id = _current_agent_id()
        agent = get_arena().get_agent(agent_id)
        if agent.role != "monitor":
            raise BadRequestError("This tool is only available for monitor agents.")
        return agent_id

    mcp = FastMCP(
        "quant-arena-ashare",
        json_response=True,
        stateless_http=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        ),
    )

    @mcp.tool()
    def get_portfolio() -> PortfolioSnapshot:
        """Get current portfolio including pending orders."""

        return get_arena().get_portfolio(_current_agent_id())

    @mcp.tool()
    def list_operations(
        agent_id: str | None = None,
        limit: int = 10,
        start: str | None = None,
        end: str | None = None,
    ) -> OperationLog:
        """List orders and fills."""

        current = _current_agent_id()
        agent = get_arena().get_agent(current)
        target = current if agent.role == "normal" else agent_id or current
        return get_arena().list_operations(
            target,
            start=parse_filter_datetime(start),
            end=parse_filter_datetime(end),
            limit=limit,
        )

    @mcp.tool()
    def list_special_events(
        agent_id: str | None = None,
        limit: int = 20,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[SpecialEvent]:
        """List corporate actions and manual position clears."""

        current = _current_agent_id()
        target = agent_id or current
        if target != current:
            require_monitor_agent()
        return get_arena().list_special_events(
            target,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
            limit=limit,
        )

    @mcp.tool()
    def get_self_metadata() -> AgentMetadata:
        """Get the current agent's metadata."""

        agent_id = _current_agent_id()
        agent = get_arena().get_agent(agent_id)
        return AgentMetadata(
            agent_id=agent_id,
            name=agent_id,
            display_name=agent.display_name,
            role=agent.role,
            currency=None,
        )

    @mcp.tool(
        description=(
            "Get current-day Sina intraday OHLCV bars for one six-digit A-share "
            "code. `start_time` is Shanghai-local HH:MM or HH:MM:SS. `interval` "
            "accepts minute or hour durations such as `5m`, `15m`, or `1h`. "
            "All agents and the order matcher share the same server-side raw-tick "
            "cache; after its configured timeout, Sina is refreshed incrementally."
        )
    )
    async def get_intraday_quotes(
        code: str,
        start_time: str,
        interval: str = "5m",
    ) -> AShareIntradayQuotes:
        _current_agent_id()
        normalized_code = _normalize_ashare_code(code)
        parsed_start = _parse_start_time(start_time)
        normalized_interval, interval_minutes = _parse_intraday_interval(interval)
        trade_date = datetime.now(SHANGHAI_TZ).date()
        arena = get_arena()
        frame = await asyncio.to_thread(
            arena.market.get_cached_intraday,
            normalized_code,
            trade_date,
        )
        bars, latest_price, as_of = _aggregate_intraday_bars(
            frame,
            normalized_code,
            trade_date,
            parsed_start,
            interval_minutes,
        )
        return AShareIntradayQuotes(
            code=normalized_code,
            name=arena.market.get_code_name(normalized_code),
            trade_date=trade_date,
            start_time=parsed_start.isoformat(),
            interval=normalized_interval,
            interval_minutes=interval_minutes,
            cache_timeout_seconds=arena.market.intraday_quote_cache_seconds,
            latest_price=latest_price,
            as_of=as_of,
            bars=bars,
        )

    @mcp.tool(
        description=(
            "Submit a pending buy or sell limit order on the A-share simulator. "
            "Settlement is T+1; buy quantity must be a multiple of 100; only "
            "main-board codes (SH 60xxxx, SZ 000/001/002/003 xxxx) are accepted. "
            "The comment should include the name of the code and briefly explain "
            "the reason in three sentences."
        )
    )
    async def submit_operation(
        code: str, side: str, quantity: int, limit_price: float, comment: str
    ) -> OrderRecord:
        return await get_arena().submit_order(
            _current_agent_id(),
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

        return get_arena().cancel_order(_current_agent_id(), order_id)

    @mcp.tool()
    def submit_daily_report(content: str) -> str:
        """Create or overwrite today's markdown daily report."""

        report = get_arena().submit_daily_report(_current_agent_id(), content)
        line_count = len(report.content.splitlines())
        char_count = len(report.content)
        return (
            f"Saved daily report for {report.trade_date.isoformat()}: "
            f"{line_count} lines, {char_count} characters."
        )

    @mcp.tool()
    def get_last_daily_report_before_today() -> DailyReport | str:
        """Return the caller's latest report whose date is before today."""

        report = get_arena().get_last_daily_report_before_today(_current_agent_id())
        if report is None:
            return "No previous daily report found."
        return report

    @mcp.tool()
    def get_agent_last_daily_report(agent_id: str) -> DailyReport | str:
        """Return the latest daily report for another agent. Monitor agents only."""

        require_monitor_agent()
        report = get_arena().get_latest_daily_report(agent_id)
        if report is None:
            return f"No daily report found for agent {agent_id}."
        return report

    @mcp.tool()
    def get_current_rankings() -> list[MonitoredAgentSnapshot]:
        """Get current rankings with portfolio snapshots. Monitor agents only."""

        require_monitor_agent()
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
                    currency=None,
                    initial_cash=agent.initial_cash,
                    return_pct=ranking.return_pct,
                    portfolio=arena.get_portfolio(ranking.agent_id),
                )
            )
        return snapshots

    @mcp.resource("arena://market-data-path")
    def market_data_path() -> str:
        return str(get_arena().market.market_data_root)

    return mcp


def wrap_mcp_with_agent_auth(
    mcp_app: ASGIApp,
    get_arena: Callable[[], ArenaService],
) -> ASGIApp:
    """Guard the mounted A-share MCP app with bearer-token auth."""

    async def authenticated_app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await mcp_app(scope, receive, send)
            return

        headers = _request_headers(scope)
        token_value = _extract_agent_token(headers)
        agent_id = None
        if token_value is not None:
            for candidate_id, agent in get_arena().list_agents():
                if agent.enabled and agent.token_secret == token_value:
                    agent_id = candidate_id
                    break
        if agent_id is None:
            response = JSONResponse(
                status_code=401,
                content={"detail": "Invalid agent token"},
            )
            await response(scope, receive, send)
            return

        next_scope = _ensure_json_accept(scope, headers)
        token = _CURRENT_AGENT_ID.set(agent_id)
        try:
            await mcp_app(next_scope, receive, send)
        finally:
            _CURRENT_AGENT_ID.reset(token)

    return authenticated_app
