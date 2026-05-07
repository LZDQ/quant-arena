"""Application configuration."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class AShareFeeConfig(BaseModel):
    """A-share trading fee configuration."""

    commission_bps: float = Field(
        default=3.0,
        description="Broker commission in basis points applied to each filled order.",
    )
    min_commission: float = Field(
        default=5.0,
        description="Minimum commission charged per filled order, in CNY.",
    )
    stamp_tax_bps: float = Field(
        default=10.0,
        description="Stamp tax in basis points applied to sell fills.",
    )


class AShareConfig(BaseModel):
    """A-share simulator settings."""

    market_data_root: str = Field(
        default=str(Path.home() / ".quant-arena" / "A-share" / "market-data"),
        description="Public root directory for shared A-share market data files.",
    )
    polling_interval_seconds: int = Field(
        default=150,
        description="Seconds between A-share market sync and order-matching cycles.",
    )
    intraday_fetch_workers: int = Field(
        default=8,
        gt=0,
        description="Thread-pool size used for parallel per-code intraday fetches.",
    )
    fees: AShareFeeConfig = Field(
        default_factory=AShareFeeConfig,
        description="Trading fee settings used by the A-share simulator.",
    )


class NapCatPrivateTargetConfig(BaseModel):
    """One private-chat target."""

    type: Literal["private"] = Field(
        default="private",
        description="NapCat destination type.",
    )
    user_id: str = Field(
        min_length=1,
        description="Target QQ user id as a string.",
    )


class NapCatGroupTargetConfig(BaseModel):
    """One group-chat target."""

    type: Literal["group"] = Field(
        default="group",
        description="NapCat destination type.",
    )
    group_id: str = Field(
        min_length=1,
        description="Target QQ group id as a string.",
    )


NapCatTargetConfig = Annotated[
    NapCatPrivateTargetConfig | NapCatGroupTargetConfig,
    Field(discriminator="type"),
]


class NapCatConfig(BaseModel):
    """NapCat QQ notification settings."""

    enabled: bool = Field(
        default=False,
        description="Whether NapCat QQ notifications are enabled.",
    )
    url: str = Field(
        default="ws://127.0.0.1:3001/",
        description="Forward WebSocket URL exposed by NapCat.",
    )
    access_token: str = Field(
        default="",
        description="Access token used for NapCat WebSocket authorization.",
    )
    notify_on_submit: bool = Field(
        default=True,
        description="Whether to notify when orders are submitted.",
    )
    notify_on_cancel: bool = Field(
        default=True,
        description="Whether to notify when orders are canceled.",
    )
    notify_on_fill: bool = Field(
        default=False,
        description="Whether to notify when orders are filled.",
    )
    request_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Timeout for one NapCat API request over WebSocket.",
    )
    reconnect_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Seconds to wait before reconnecting after a NapCat connection failure.",
    )
    destinations: dict[str, NapCatTargetConfig] = Field(
        default_factory=dict,
        description="Named QQ destinations available for per-agent notification routing.",
    )


class QQOpenGroupTargetConfig(BaseModel):
    """One QQ Open Platform group-chat target."""

    type: Literal["group"] = Field(
        default="group",
        description="QQ Open Platform destination type.",
    )
    group_openid: str = Field(
        min_length=1,
        description="Target QQ Open Platform group openid as a string.",
    )

class QQOpenConfig(BaseModel):
    """QQ Open Platform notification settings."""

    enabled: bool = Field(
        default=False,
        description="Whether QQ Open Platform notifications are enabled.",
    )
    app_id: str = Field(
        default="",
        description="QQ Open Platform bot AppID.",
    )
    client_secret: str = Field(
        default="",
        description="QQ Open Platform bot AppSecret.",
    )
    sandbox: bool = Field(
        default=True,
        description="Whether to use the QQ Open Platform sandbox API endpoint.",
    )
    notify_on_submit: bool = Field(
        default=True,
        description="Whether to notify when orders are submitted.",
    )
    notify_on_cancel: bool = Field(
        default=True,
        description="Whether to notify when orders are canceled.",
    )
    notify_on_fill: bool = Field(
        default=False,
        description="Whether to notify when orders are filled.",
    )
    request_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Timeout for one QQ Open Platform API request.",
    )
    retry_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Seconds to wait before retrying a failed QQ Open Platform request.",
    )
    destinations: dict[str, QQOpenGroupTargetConfig] = Field(
        default_factory=dict,
        description="Named QQ Open Platform destinations available for per-agent notification routing.",
    )


class IBConnectionConfig(BaseModel):
    """One IB Gateway / TWS endpoint."""

    host: str = Field(
        default="127.0.0.1",
        description="Hostname or IP of IB Gateway or TWS.",
    )
    port: int = Field(
        description="TCP port. IB Gateway: 4001 live / 4002 paper. TWS: 7496 live / 7497 paper.",
    )
    client_id: int = Field(
        default=2,
        description="ib_insync clientId for this connection.",
    )


class IBConfig(BaseModel):
    """Interactive Brokers paper/real trading settings."""

    enabled: bool = Field(
        default=False,
        description="Whether the IB integration is enabled.",
    )
    paper: IBConnectionConfig = Field(
        default_factory=lambda: IBConnectionConfig(port=4002, client_id=2),
        description="IB Gateway / TWS endpoint for the paper trading account.",
    )
    real: IBConnectionConfig = Field(
        default_factory=lambda: IBConnectionConfig(port=4001, client_id=3),
        description="IB Gateway / TWS endpoint for the real trading account.",
    )
    paper_token: str = Field(
        default="",
        description="Bearer token an MCP client must present to access the paper account.",
    )
    real_token: str = Field(
        default="",
        description="Bearer token an MCP client must present to access the real account.",
    )
    request_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Timeout for one IB MCP tool call.",
    )
    default_exchange: str = Field(
        default="SMART",
        description="Default exchange used when resolving stock contracts.",
    )
    default_currency: str = Field(
        default="USD",
        description="Default currency used when resolving stock contracts.",
    )


class FutumooHKFeeConfig(BaseModel):
    """HK-side fee configuration for the Futumoo paper-trading arena."""

    commission_bps: float = Field(
        default=0.0,
        description="Broker commission in basis points applied to each filled HK order, in HKD.",
    )
    min_commission: float = Field(
        default=0.0,
        description="Minimum commission charged per filled HK order, in HKD.",
    )
    stamp_tax_bps: float = Field(
        default=10.0,
        description="HK stamp duty in basis points applied to both buy and sell consideration. Default 0.10%.",
    )


class FutumooUSFeeConfig(BaseModel):
    """US-side fee configuration for the Futumoo paper-trading arena."""

    commission_bps: float = Field(
        default=0.0,
        description="Broker commission in basis points applied to each filled US order, in USD.",
    )
    min_commission: float = Field(
        default=0.0,
        description="Minimum commission charged per filled US order, in USD.",
    )


class FutumooConfig(BaseModel):
    """Futumoo HK/US paper-trading arena settings.

    Orders are matched against `last_price` snapshots polled from Futu OpenD,
    not filled instantly. Each agent holds two cash buckets (HKD, USD) and
    two position books. Symbols must carry the region prefix `HK.` or `US.`.
    """

    host: str = Field(
        default="127.0.0.1",
        description="Hostname or IP of the Futu OpenD gateway.",
    )
    port: int = Field(
        default=11111,
        description="TCP port of the Futu OpenD gateway.",
    )
    polling_interval_seconds: int = Field(
        default=30,
        description="Seconds between snapshot refresh / pending-order match cycles.",
    )
    fx_hkd_per_usd: float = Field(
        default=7.80,
        gt=0,
        description=(
            "Static HKD-per-USD conversion rate used to express two-currency portfolios "
            "as a single USD-equivalent total for rankings and the PDT equity threshold."
        ),
    )
    pdt_equity_threshold_usd: float = Field(
        default=25_000.0,
        ge=0,
        description=(
            "FINRA pattern-day-trader minimum equity. While the agent's total "
            "USD-equivalent equity is below this value, the US arena allows at "
            "most 3 day-trades in any rolling 5 US-business-day window."
        ),
    )
    pdt_max_day_trades: int = Field(
        default=3,
        ge=0,
        description="Max day-trades permitted in the rolling 5 US-business-day window when below the PDT equity threshold.",
    )
    pdt_window_business_days: int = Field(
        default=5,
        gt=0,
        description="Length of the rolling US-business-day window the PDT counter looks back over.",
    )
    hk_fees: FutumooHKFeeConfig = Field(
        default_factory=FutumooHKFeeConfig,
        description="Fee schedule applied to HK fills.",
    )
    us_fees: FutumooUSFeeConfig = Field(
        default_factory=FutumooUSFeeConfig,
        description="Fee schedule applied to US fills.",
    )


class AppConfig(BaseModel):
    """Top-level server configuration."""

    host: str = Field(
        default="127.0.0.1",
        description="Host interface the FastAPI server binds to.",
    )
    port: int = Field(
        default=18792,
        description="TCP port the FastAPI server listens on.",
    )
    ashare: AShareConfig = Field(
        default_factory=AShareConfig,
        description="A-share simulator settings.",
    )
    futumoo: FutumooConfig = Field(
        default_factory=FutumooConfig,
        description="Futumoo offline paper trading settings.",
    )
    napcat: NapCatConfig = Field(
        default_factory=NapCatConfig,
        description="NapCat QQ notification settings.",
    )
    qq_open: QQOpenConfig = Field(
        default_factory=QQOpenConfig,
        description="QQ Open Platform notification settings.",
    )
    ib: IBConfig = Field(
        default_factory=IBConfig,
        description="Interactive Brokers paper/real trading settings.",
    )


class AgentConfig(BaseModel):
    """One managed trading agent."""

    display_name: str = Field(
        description="Human-readable name shown in the UI and rankings.",
    )
    token_secret: str = Field(
        description="Shared secret value expected in the configured authentication header.",
    )
    initial_cash: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Single-currency starting balance. Used by the A-share arena (CNY, "
            "must be > 0 there) and as the USD-equivalent total for ranking on "
            "the Futumoo arena, where it is derived from `initial_cash_hkd` and "
            "`initial_cash_usd` inside `FutumooArenaService.add_agent`."
        ),
    )
    initial_cash_hkd: float | None = Field(
        default=None,
        description="Starting HKD cash on the Futumoo arena. Required when registering a Futumoo agent.",
    )
    initial_cash_usd: float | None = Field(
        default=None,
        description="Starting USD cash on the Futumoo arena. Required when registering a Futumoo agent.",
    )
    enabled: bool = Field(
        default=True,
        description="Whether the agent is enabled and available for use.",
    )
    role: Literal["normal", "monitor"] = Field(
        default="normal",
        description="Agent role. monitor agents can inspect other agents through MCP tools.",
    )
    napcat_notify_targets: list[str] = Field(
        default_factory=list,
        description="Named NapCat notification destinations enabled for this agent.",
    )
    qq_open_notify_targets: list[str] = Field(
        default_factory=list,
        description="Named QQ Open Platform notification destinations enabled for this agent.",
    )


def _write_default_app_config(path: Path) -> AppConfig:
    config = AppConfig()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config.model_dump(mode="json"), handle, ensure_ascii=False, indent="\t")
        handle.write("\n")
    return config


def load_app_config(path: Path) -> AppConfig:
    """Load the app config, writing defaults when missing."""

    if not path.exists():
        return _write_default_app_config(path)
    with path.open("r", encoding="utf-8") as handle:
        return AppConfig.model_validate(json.load(handle))
