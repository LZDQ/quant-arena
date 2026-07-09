"""Application configuration."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    """Environment settings, read from `QUANT_ARENA_*` env vars at app creation.

    Everything else (markets, fees, notifiers, ...) is file configuration in
    `AppConfig`, loaded from the default config path.
    """

    model_config = SettingsConfigDict(env_prefix="QUANT_ARENA_")

    url_prefix: str = Field(
        default="",
        description="URL prefix the app is mounted under, e.g. /quant-arena. Empty serves at the root.",
    )


class AShareFeeConfig(BaseModel):
    """A-share trading fee configuration."""

    commission_bps: float = Field(
        default=3.0,
        description="Broker commission in basis points applied to each filled order.",
    )
    min_commission: float = Field(
        default=5.0,
        description="Minimum commission charged per filled order.",
    )
    stamp_tax_bps: float = Field(
        default=10.0,
        description="Stamp tax in basis points applied to sell fills.",
    )


class AShareConfig(BaseModel):
    """A-share simulator settings."""

    enabled: bool = Field(
        default=True,
        description="Whether the A-share arena is enabled. When false, its routes, MCP mount, and background tasks are skipped.",
    )
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


class FutumooCNFeeConfig(BaseModel):
    """Mainland China-side fee configuration for the Futumoo paper-trading arena."""

    commission_bps: float = Field(
        default=0.0,
        description="Broker commission in basis points applied to each filled CN order, in CNY.",
    )
    min_commission: float = Field(
        default=0.0,
        description="Minimum commission charged per filled CN order, in CNY.",
    )
    stamp_tax_bps: float = Field(
        default=5.0,
        description="Mainland stock stamp tax in basis points applied to sell fills.",
    )


class FutumooConfig(BaseModel):
    """Futumoo HK/US/CN paper-trading arena settings.

    Orders are matched against `last_price` snapshots polled from Futu OpenD,
    not filled instantly. Each agent chooses one currency (`HKD`, `USD`, or
    `CNY`), which selects the HK, US, or mainland China region. Symbols must
    carry the region prefix `HK.`, `US.`, `SH.`, or `SZ.`.
    """

    enabled: bool = Field(
        default=False,
        description="Whether the Futumoo arena is enabled. When false, its routes, MCP mount, and background tasks are skipped.",
    )
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
    cn_fees: FutumooCNFeeConfig = Field(
        default_factory=FutumooCNFeeConfig,
        description="Fee schedule applied to mainland China fills.",
    )


class EODHDFeeConfig(BaseModel):
    """Fee configuration for the EODHD paper-trading arena."""

    commission_bps: float = Field(
        default=0.0,
        description="Broker commission in basis points applied to each EODHD fill.",
    )
    min_commission: float = Field(
        default=0.0,
        description="Minimum commission charged per EODHD fill.",
    )


class EODHDConfig(BaseModel):
    """EODHD all-in-one market-data and paper-trading arena settings."""

    enabled: bool = Field(
        default=False,
        description="Whether the EODHD arena is enabled. When false, its routes, MCP mount, and background tasks are skipped.",
    )
    api_token: str = Field(
        default="demo",
        description="EODHD API token. The demo token is useful only for smoke checks.",
    )
    market_data_root: str = Field(
        default=str(Path.home() / ".quant-arena" / "eodhd" / "market-data"),
        description="Public root directory for EODHD market data files. Must not be the A-share baostock directory.",
    )
    exchanges: list[str] = Field(
        default_factory=lambda: ["US"],
        description="EODHD exchange codes to persist, for example US, NASDAQ, NYSE, LSE, XETRA.",
    )
    allowed_currencies: list[str] = Field(
        default_factory=lambda: ["USD", "HKD", "CNY"],
        description="Currencies agents may choose in the EODHD paper arena.",
    )
    default_currency: str = Field(
        default="USD",
        description="Currency used when creating an EODHD agent without an explicit currency.",
    )
    polling_interval_seconds: int = Field(
        default=60,
        description="Seconds between EODHD live-price match cycles and market-data finalization checks.",
    )
    daily_finalize_utc: str = Field(
        default="01:30",
        description="UTC HH:MM time after which yesterday's bulk EODHD daily bars are persisted.",
    )
    five_min_finalize_utc: str = Field(
        default="02:00",
        description="UTC HH:MM time after which yesterday's EODHD 5-minute bars are persisted.",
    )
    fees: EODHDFeeConfig = Field(
        default_factory=EODHDFeeConfig,
        description="Fee schedule applied to EODHD fills.",
    )


class AppConfig(BaseModel):
    """Top-level server configuration. Host/port are uvicorn CLI flags, not config."""

    ashare: AShareConfig = Field(
        default_factory=AShareConfig,
        description="A-share simulator settings.",
    )
    futumoo: FutumooConfig = Field(
        default_factory=FutumooConfig,
        description="Futumoo offline paper trading settings.",
    )
    eodhd: EODHDConfig = Field(
        default_factory=EODHDConfig,
        description="EODHD all-in-one market-data and paper-trading settings.",
    )
    napcat: NapCatConfig = Field(
        default_factory=NapCatConfig,
        description="NapCat QQ notification settings.",
    )


class AgentConfig(BaseModel):
    """One managed trading agent.

    Currency is arena-local. A-share leaves it unset; Futumoo agents set
    `HKD` (only `HK.<code>` symbols allowed), `USD` (only `US.<ticker>`
    symbols allowed), or `CNY` (only `SH.<code>` / `SZ.<code>` symbols
    allowed). EODHD agents use arena-configured currencies.
    """

    display_name: str = Field(
        description="Human-readable name shown in the UI and rankings.",
    )
    token_secret: str = Field(
        description="Shared secret value expected in the configured authentication header.",
    )
    initial_cash: float = Field(
        gt=0,
        description="Starting cash balance.",
    )
    currency: str | None = Field(
        default=None,
        description="Arena-local trading currency. Futumoo uses HKD, USD, or CNY; EODHD uses configured currencies; A-share leaves this unset.",
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
        description="Named NapCat notification destinations enabled for this agent's order notifications.",
    )
    daily_report_notify_targets: list[str] = Field(
        default_factory=list,
        description="Named NapCat destinations that receive this agent's daily-report PDF (NapCat only).",
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


def save_app_config(path: Path, config: AppConfig) -> None:
    """Atomically rewrite `path` with `config`. Used by runtime mutations
    (e.g. arena enable/disable toggles) so the new value survives a restart.
    """
    payload = json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent="\t")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp_path.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")
    tmp_path.replace(path)
