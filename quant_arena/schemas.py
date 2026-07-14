"""FastAPI request and response models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from quant_arena.config import NapCatTargetConfig
from quant_arena.models import DailyReportSummary, EquityPoint, OrderRecord


class PositionView(BaseModel):
    """API view of one portfolio position."""

    code: str
    name: str | None = None
    quantity: int
    sellable_quantity: int
    avg_cost: float | None
    market_price: float | None = None
    market_value: float = 0.0
    unrealized_pnl: float | None = 0.0
    intraday_as_of: datetime | None = None


class PortfolioResponse(BaseModel):
    """Portfolio plus pending orders. `currency` is arena-local and may be unset."""

    agent_id: str
    currency: str | None = None
    cash: float
    market_value: float
    total_equity: float
    realized_pnl: float | None
    unrealized_pnl: float | None
    positions: list[PositionView]
    pending_orders: list[OrderRecord]
    as_of: datetime | None = None
    day_return_pct: float | None = None


class OperationListResponse(BaseModel):
    """Orders with inline execution details."""

    orders: list[OrderRecord]


class PathsResponse(BaseModel):
    """Resolved runtime paths."""

    config_path: str
    global_market_data_root: str
    agents_root: str
    market_data_root: str
    eodhd_agents_root: str | None = None
    eodhd_market_data_root: str | None = None


class CreateAgentRequest(BaseModel):
    """Request to create a new agent.

    `currency` is arena-local. A-share ignores it and stores no currency.
    Futumoo requires `HKD`, `USD`, or `CNY`.
    """

    agent_id: str
    display_name: str
    initial_cash: float = Field(gt=0)
    currency: str | None = None
    enabled: bool = True
    amnesia: bool = False
    role: Literal["normal", "monitor"] = "normal"


class AgentResponse(BaseModel):
    """API view of one agent plus its directory-based id."""

    agent_id: str
    display_name: str
    initial_cash: float
    currency: str | None = None
    enabled: bool
    amnesia: bool
    role: Literal["normal", "monitor"]
    napcat_notify_targets: list[str] = Field(default_factory=list)
    daily_report_notify_targets: list[str] = Field(default_factory=list)


class AgentCreatedResponse(BaseModel):
    """Create-agent response with one-time token display."""

    agent: AgentResponse
    token_secret: str


class SetAgentAmnesiaRequest(BaseModel):
    """Update whether one agent can see memory from previous days."""

    amnesia: bool


class ArenaStatus(BaseModel):
    """Whether one arena is enabled at startup. Persisted in config.json."""

    slug: Literal["ashare", "futumoo", "eodhd"]
    label: str
    enabled: bool
    data_provider_only: bool


class ToggleArenaRequest(BaseModel):
    """Lifecycle settings persisted by `PATCH /api/arenas/{slug}`."""

    enabled: bool
    data_provider_only: bool | None = None


class ToggleArenaResponse(BaseModel):
    """Response body for `PATCH /api/arenas/{slug}`.

    `restart_required` is always true on success: lifecycle settings are
    checked once at startup to gate route registration, MCP mounts, and
    background tasks, so the new state takes effect on the next server restart.
    """

    status: ArenaStatus
    restart_required: bool = True


class DailyReportPage(BaseModel):
    """Paginated listing of an agent's daily reports (newest first)."""

    items: list[DailyReportSummary]
    total: int
    page: int
    page_size: int


class AgentSnapshotResponse(BaseModel):
    """Grouped frontend view for one agent."""

    agent: AgentResponse
    portfolio: PortfolioResponse
    operations: OperationListResponse
    equity: list[EquityPoint]


class NotificationDestinationsResponse(BaseModel):
    """Global notification destination catalog returned to the frontend.

    Mirrors `AppConfig.napcat.destinations` plus the `enabled` flag so the
    UI can grey-out cards while the channel is off.
    """

    napcat_enabled: bool
    napcat_destinations: dict[str, NapCatTargetConfig]


class FutumooUserInfoResponse(BaseModel):
    """Logged-in Futu OpenD user and connection state."""

    nick_name: str | None = None
    avatar_url: str | None = None
    user_id: str | None = None
    login_user_id: str | None = None
    user_attr: str | None = None
    api_level: str | None = None
    hk_qot_right: str | None = None
    hk_option_qot_right: str | None = None
    hk_future_qot_right: str | None = None
    us_qot_right: str | None = None
    us_option_qot_right: str | None = None
    us_future_qot_right: str | None = None
    cn_qot_right: str | None = None
    sg_future_qot_right: str | None = None
    jp_future_qot_right: str | None = None
    us_future_qot_right_cme: str | None = None
    us_future_qot_right_cbot: str | None = None
    us_future_qot_right_nymex: str | None = None
    us_future_qot_right_comex: str | None = None
    us_future_qot_right_cboe: str | None = None
    is_need_agree_disclaimer: bool | None = None
    update_type: str | None = None
    web_key: str | None = None
    sub_quota: int | None = None
    history_kl_quota: int | None = None
    qot_logined: bool
    trd_logined: bool
    program_status_type: str | None = None
    program_status_desc: str | None = None
    server_ver: str | None = None
    market_hk: str | None = None
    market_us: str | None = None
    market_sh: str | None = None
    market_sz: str | None = None


class FutumooSubscribedSymbolResponse(BaseModel):
    """One recently accessed Futu real-time quote subscription."""

    code: str
    name: str | None = None


class FutumooSubscriptionStatusResponse(BaseModel):
    """Local Futumoo QUOTE subscription pool status."""

    subscribed_count: int = Field(ge=0)
    subscription_limit: int = Field(gt=0)
    latest_accessed_symbols: list[FutumooSubscribedSymbolResponse]


class EODHDUserInfoResponse(BaseModel):
    """Configured EODHD package/credential/cache status for the page header."""

    credential_status: Literal["configured", "missing"]
    package_version: str
    configured_exchanges: list[str]
    code_names_count: int


class SetNapCatDestinationsRequest(BaseModel):
    """Replace the full `napcat.destinations` mapping in app config."""

    destinations: dict[str, NapCatTargetConfig]


class AgentNotificationTargets(BaseModel):
    """Per-agent enabled subset of the global destination keys.

    `napcat` routes order notifications; `daily_report` routes the
    daily-report PDF and references NapCat destination keys (NapCat only).
    """

    napcat: list[str] = Field(default_factory=list)
    daily_report: list[str] = Field(default_factory=list)


class ManualClearPositionsRequest(BaseModel):
    """Request body for manually clearing an agent's positions."""

    comment: str = Field(min_length=1, max_length=200)
    keep_unrealized_pnl: bool
    keep_realized_pnl: bool
