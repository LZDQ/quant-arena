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
        gt=0,
        description="Starting cash balance, in CNY, used when the agent state is first created.",
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
