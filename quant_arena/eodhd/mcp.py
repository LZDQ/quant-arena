"""EODHD MCP server: agent-token auth on top of the EODHD paper arena."""

import asyncio
from contextvars import ContextVar
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Literal
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from quant_arena.errors import BadRequestError
from quant_arena.eodhd.arena import EODHDArenaService
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
    "quant_arena_eodhd_current_agent_id", default=None
)
_AGENT_TOKEN_HEADER = "quant-arena-token"
_MAX_LIVE_QUOTE_CODES = 100
_MARKET_TIMEZONES = {
    "US": "America/New_York",
    "HK": "Asia/Hong_Kong",
    "SHG": "Asia/Shanghai",
    "SHE": "Asia/Shanghai",
}


class EODHDLiveQuote(BaseModel):
    """One EODHD live quote row returned through MCP."""

    code: str
    name: str | None = None
    exchange: str | None = None
    currency: str | None = None
    instrument_type: str | None = None
    country: str | None = None
    last_price: float | None = None
    update_time: datetime | None = None
    status: Literal["ok", "not_found"] = "not_found"


class EODHDIntradayBar(BaseModel):
    """One EODHD intraday bar normalized to UTC plus market-local time."""

    code: str
    exchange: str
    datetime_utc: datetime
    datetime_local: datetime
    local_time: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: int | None = None


class EODHDIntradayHistory(BaseModel):
    """Intraday history window for one EODHD symbol."""

    code: str
    exchange: str
    timezone: str
    trade_date: date
    start_time: str
    interval_minutes: int = Field(gt=0)
    start_utc: datetime
    end_utc: datetime
    bar_interval: str = "5m"
    bars: list[EODHDIntradayBar]


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
        raise RuntimeError("No authenticated eodhd agent in MCP request context")
    return agent_id


def _normalize_eodhd_code(code: str) -> str:
    text = code.strip()
    if "." not in text:
        raise BadRequestError(
            "EODHD symbols must include an exchange suffix, for example AAPL.US."
        )
    symbol, exchange = text.rsplit(".", 1)
    symbol = symbol.strip()
    exchange = exchange.strip().upper()
    if not symbol or not exchange:
        raise BadRequestError(
            "EODHD symbols must include both a symbol and exchange suffix."
        )
    return f"{symbol}.{exchange}"


def _dedupe_codes(codes: list[str]) -> list[str]:
    if not codes:
        raise BadRequestError("At least one EODHD symbol is required.")
    normalized: list[str] = []
    seen: set[str] = set()
    for code in codes:
        value = _normalize_eodhd_code(code)
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    if len(normalized) > _MAX_LIVE_QUOTE_CODES:
        raise BadRequestError(
            f"At most {_MAX_LIVE_QUOTE_CODES} EODHD symbols can be requested at once."
        )
    return normalized


def _exchange_from_code(code: str) -> str:
    return code.rsplit(".", 1)[1].upper()


def _timezone_name_for_exchange(exchange: str) -> str:
    value = _MARKET_TIMEZONES.get(exchange)
    if value is None:
        raise BadRequestError(
            f"No hardcoded market timezone configured for EODHD exchange {exchange!r}."
        )
    return value


def _parse_hhmm(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise BadRequestError(
            f"Expected start_time in HH:MM format, for example 09:50; got {value!r}."
        ) from exc


def _parse_datetime_value(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return datetime.fromtimestamp(float(raw), timezone.utc)
    text = str(raw).strip()
    if not text:
        return None
    try:
        if text.isdigit():
            return datetime.fromtimestamp(float(text), timezone.utc)
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def create_eodhd_mcp_server(
    get_arena: Callable[[], EODHDArenaService],
) -> FastMCP:
    """Create the EODHD paper-trading MCP server."""

    def parse_filter_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment

    def require_monitor_agent() -> str:
        agent_id = _current_agent_id()
        agent = get_arena().get_agent(agent_id)
        if agent.role != "monitor":
            raise BadRequestError("This tool is only available for monitor agents.")
        return agent_id

    mcp = FastMCP(
        "quant-arena-eodhd",
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
        """List special account events such as manual position clears."""

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
            currency=agent.currency,
        )

    @mcp.tool(
        description=(
            "Get batched live EODHD quotes for provider symbols such as "
            "`AAPL.US` or `0005.HK`. The EODHD API key stays server-side. "
            "Returns one row per requested symbol with `status='not_found'` "
            "when EODHD does not return a usable live price."
        )
    )
    async def get_live_quotes(codes: list[str]) -> list[EODHDLiveQuote]:
        _current_agent_id()
        normalized_codes = _dedupe_codes(codes)
        arena = get_arena()
        snapshots = await asyncio.to_thread(
            arena.market.get_snapshots,
            normalized_codes,
        )
        quotes: list[EODHDLiveQuote] = []
        for code in normalized_codes:
            metadata = arena.market.get_code_metadata(code)
            exchange = metadata.get("exchange") or _exchange_from_code(code)
            row = snapshots.get(code)
            if row is None:
                quotes.append(
                    EODHDLiveQuote(
                        code=code,
                        name=metadata.get("name"),
                        exchange=exchange,
                        currency=metadata.get("currency"),
                        instrument_type=metadata.get("type"),
                        country=metadata.get("country"),
                    )
                )
                continue
            quotes.append(
                EODHDLiveQuote(
                    code=code,
                    name=metadata.get("name") or _text_or_none(row.get("name")),
                    exchange=exchange,
                    currency=metadata.get("currency"),
                    instrument_type=metadata.get("type"),
                    country=metadata.get("country"),
                    last_price=_float_or_none(row.get("last_price")),
                    update_time=_parse_datetime_value(row.get("update_time")),
                    status="ok",
                )
            )
        return quotes

    @mcp.tool(
        description=(
            "Get EODHD 5-minute intraday history for one symbol over a "
            "market-local time window. `start_time` must use HH:MM format, "
            "for example `09:50`. `interval_minutes` is the local window length "
            "and defaults to 5. US symbols use America/New_York; HK symbols use "
            "Asia/Hong_Kong."
        )
    )
    async def get_intraday_history(
        code: str,
        start_time: str,
        trade_date: str | None = None,
        interval_minutes: int = 5,
    ) -> EODHDIntradayHistory:
        _current_agent_id()
        if interval_minutes <= 0:
            raise BadRequestError("interval_minutes must be positive.")
        if interval_minutes > 1440:
            raise BadRequestError("interval_minutes must be no more than one day.")

        normalized_code = _normalize_eodhd_code(code)
        exchange = _exchange_from_code(normalized_code)
        timezone_name = _timezone_name_for_exchange(exchange)
        market_zone = ZoneInfo(timezone_name)
        if trade_date is None:
            local_day = datetime.now(market_zone).date()
        else:
            try:
                local_day = date.fromisoformat(trade_date)
            except ValueError as exc:
                raise BadRequestError(
                    f"Expected trade_date in YYYY-MM-DD format; got {trade_date!r}."
                ) from exc
        local_start_time = _parse_hhmm(start_time)
        start_local = datetime.combine(local_day, local_start_time, tzinfo=market_zone)
        end_local = start_local + timedelta(minutes=interval_minutes)
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)

        frame = await asyncio.to_thread(
            get_arena().market.fetch_intraday_window,
            normalized_code,
            start_utc,
            end_utc,
        )
        bars: list[EODHDIntradayBar] = []
        for raw_row in frame.to_dict(orient="records"):
            row: dict[str, object] = {
                str(key): value for key, value in raw_row.items()
            }
            raw_close = _float_or_none(row.get("close"))
            raw_datetime = _text_or_none(row.get("datetime_utc"))
            if raw_close is None or raw_datetime is None:
                continue
            moment_utc = _parse_datetime_value(raw_datetime)
            if moment_utc is None:
                continue
            moment_local = moment_utc.astimezone(market_zone)
            bars.append(
                EODHDIntradayBar(
                    code=normalized_code,
                    exchange=exchange,
                    datetime_utc=moment_utc,
                    datetime_local=moment_local,
                    local_time=moment_local.strftime("%H:%M"),
                    open=_float_or_none(row.get("open")),
                    high=_float_or_none(row.get("high")),
                    low=_float_or_none(row.get("low")),
                    close=raw_close,
                    volume=_int_or_none(row.get("volume")),
                )
            )

        return EODHDIntradayHistory(
            code=normalized_code,
            exchange=exchange,
            timezone=timezone_name,
            trade_date=local_day,
            start_time=start_time,
            interval_minutes=interval_minutes,
            start_utc=start_utc,
            end_utc=end_utc,
            bars=bars,
        )

    @mcp.tool(
        description=(
            "Submit a limit-price buy or sell order on the EODHD paper arena. "
            "Use EODHD provider symbols with an exchange suffix, for example "
            "`AAPL.US` or `0005.HK`. Each agent trades in a single configured "
            "currency, but EODHD itself is used as a data source, so there is "
            "no broker-region routing. Orders are queued as pending and matched "
            "against live `last_price` snapshots from the EODHD API. Invalid "
            "orders are rejected at submission and never appear in the order log."
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
                    currency=agent.currency,
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


def wrap_eodhd_mcp_with_agent_auth(
    mcp_app: ASGIApp,
    get_arena: Callable[[], EODHDArenaService],
) -> ASGIApp:
    """Bearer-token wrapper that resolves the calling agent for the EODHD MCP."""

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
                content={"detail": "Invalid eodhd agent token"},
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
