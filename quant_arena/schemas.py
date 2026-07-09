"""FastAPI request and response models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from quant_arena.config import NapCatTargetConfig
from quant_arena.models import DailyReportSummary, EquityPoint, FillRecord, OrderRecord


class PositionView(BaseModel):
    """API view of one portfolio position."""

    code: str
    name: str | None = None
    quantity: int
    sellable_quantity: int
    avg_cost: float
    market_price: float | None = None
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    intraday_as_of: datetime | None = None


class PortfolioResponse(BaseModel):
    """Portfolio plus pending orders. `currency` is arena-local and may be unset."""

    agent_id: str
    currency: str | None = None
    cash: float
    market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    positions: list[PositionView]
    pending_orders: list[OrderRecord]
    as_of: datetime | None = None
    day_return_pct: float | None = None


class OperationListResponse(BaseModel):
    """Combined operations payload."""

    orders: list[OrderRecord]
    fills: list[FillRecord]


class PathsResponse(BaseModel):
    """Resolved runtime paths."""

    config_path: str
    agents_root: str
    market_data_root: str


class CreateAgentRequest(BaseModel):
    """Request to create a new agent.

    `currency` is arena-local. A-share ignores it and stores no currency.
    Futumoo requires `HKD` or `USD`.
    """

    agent_id: str
    display_name: str
    initial_cash: float = Field(gt=0)
    currency: str | None = None
    enabled: bool = True
    role: Literal["normal", "monitor"] = "normal"


class AgentResponse(BaseModel):
    """API view of one agent plus its directory-based id."""

    agent_id: str
    display_name: str
    initial_cash: float
    currency: str | None = None
    enabled: bool
    role: Literal["normal", "monitor"]
    napcat_notify_targets: list[str] = Field(default_factory=list)
    daily_report_notify_targets: list[str] = Field(default_factory=list)


class AgentCreatedResponse(BaseModel):
    """Create-agent response with one-time token display."""

    agent: AgentResponse
    token_secret: str


class ArenaStatus(BaseModel):
    """Whether one arena is enabled at startup. Persisted in config.json."""

    slug: Literal["ashare", "futumoo"]
    label: str
    enabled: bool


class ToggleArenaRequest(BaseModel):
    """Request body for `PATCH /api/arenas/{slug}`."""

    enabled: bool


class ToggleArenaResponse(BaseModel):
    """Response body for `PATCH /api/arenas/{slug}`.

    `restart_required` is always true on success: the enable flag is checked
    once at startup to gate route registration, MCP mounts, and background
    tasks, so the new state takes effect on the next server restart.
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
