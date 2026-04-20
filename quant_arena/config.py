"""Application configuration."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator


class FeeConfig(BaseModel):
    """Trading fee configuration."""

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
    agents_root: str = Field(
        default=str(Path.home() / ".quant-arena" / "agents"),
        description="Private root directory for per-agent config and state files.",
    )
    market_data_root: str = Field(
        default=str(Path.home() / ".quant-arena" / "market-data"),
        description="Public root directory for shared market data files, usually configured to a shared directory.",
    )
    enable_code_name_refresh: bool = Field(
        default=False,
        description="Whether the server should automatically refresh the shared codes.csv reference file when it becomes stale.",
    )
    polling_interval_seconds: int = Field(
        default=60,
        description="Seconds between background market sync and order-matching cycles.",
    )
    enable_background_polling: bool = Field(
        default=True,
        description="Whether the server should run periodic market sync and order matching in the background.",
    )
    fees: FeeConfig = Field(
        default_factory=FeeConfig,
        description="Trading fee settings used by the simulation engine.",
    )
    napcat: NapCatConfig = Field(
        default_factory=NapCatConfig,
        description="NapCat QQ notification settings.",
    )
    qq_open: QQOpenConfig = Field(
        default_factory=QQOpenConfig,
        description="QQ Open Platform notification settings.",
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
    sell_constraint: Literal["t_plus_one"] = Field(
        default="t_plus_one",
        description="Sell constraint policy enforced by the simulator.",
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
