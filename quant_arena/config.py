"""Application configuration."""

import json
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, Field, model_validator
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


class ArenaBaseConfig(BaseModel):
    """Lifecycle settings shared by every arena."""

    enabled: bool = Field(
        default=False,
        description="Whether the arena's market-data provider is enabled. When false, its provider, agent runtime, routes, MCP mount, and background tasks are skipped.",
    )
    data_provider_only: bool = Field(
        default=False,
        description="Start only the market-data provider side of an enabled arena. Provider persistence continues, while the agent registry, agent APIs, MCP mount, order submission, and order matching are disabled.",
    )

    @property
    def agent_runtime_enabled(self) -> bool:
        """Whether agent registration and paper trading should be started."""

        return self.enabled and not self.data_provider_only


class PersistentMarketDataArenaConfig(ArenaBaseConfig):
    """Shared market-data path configuration for persistent providers."""

    arena_id: ClassVar[str]

    market_data_root: str | None = Field(
        default=None,
        description="Optional arena-specific market-data directory. When unset, use <global market_data_root>/<arena id>.",
    )

    def resolve_market_data_root(self, global_market_data_root: str) -> Path:
        """Resolve the override or the arena directory below the global root."""

        if self.market_data_root is not None:
            return Path(self.market_data_root).expanduser().resolve()
        return (Path(global_market_data_root).expanduser() / self.arena_id).resolve()


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


class AShareConfig(PersistentMarketDataArenaConfig):
    """A-share simulator settings."""

    arena_id: ClassVar[str] = "ashare"

    polling_interval_seconds: int = Field(
        default=150,
        description="Seconds between A-share market sync and order-matching cycles.",
    )
    intraday_fetch_workers: int = Field(
        default=8,
        gt=0,
        description="Thread-pool size used for parallel per-code intraday fetches.",
    )
    intraday_quote_cache_seconds: int = Field(
        default=60,
        ge=0,
        description=(
            "Seconds to reuse the shared current-day Sina tick cache used by "
            "A-share order matching and MCP intraday quote queries. Set to 0 "
            "to refresh incrementally on every request."
        ),
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


class FutumooConfig(ArenaBaseConfig):
    """Futumoo HK/US/CN paper-trading arena settings.

    Orders are matched against real-time `last_price` pushes from Futu OpenD.
    Each agent chooses one currency (`HKD`, `USD`, or
    `CNY`), which selects the HK, US, or mainland China region. Symbols must
    carry the region prefix `HK.`, `US.`, `SH.`, or `SZ.`.
    """

    host: str = Field(
        default="127.0.0.1",
        description="Hostname or IP of the Futu OpenD gateway.",
    )
    port: int = Field(
        default=11111,
        description="TCP port of the Futu OpenD gateway.",
    )
    live_quote_cache_seconds: int = Field(
        default=60,
        ge=0,
        description=(
            "Seconds to cache Futu get_market_snapshot results returned by "
            "the MCP get_live_quotes tool. Set to 0 to disable the cache."
        ),
    )
    polling_interval_seconds: int = Field(
        default=30,
        description=(
            "Seconds between session-state maintenance checks. Quote updates "
            "and order matching are event-driven."
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
    cn_fees: FutumooCNFeeConfig = Field(
        default_factory=FutumooCNFeeConfig,
        description="Fee schedule applied to mainland China fills.",
    )


class EODHDFeeConfig(BaseModel):
    """Fee configuration for the EODHD paper-trading arena."""

    commission_bps: float = Field(
        default=3.0,
        description="Broker commission in basis points applied to each EODHD fill. The default models a 0.03% retail-broker commission.",
    )
    min_commission: float = Field(
        default=3.0,
        description="Minimum commission charged per EODHD fill, in the agent's configured currency.",
    )


class EODHDBarScheduleConfig(BaseModel):
    """Background persistence settings for one EODHD bar kind."""

    enabled: bool = Field(
        default=False,
        description="Whether this bar kind is persisted automatically.",
    )
    finalize_utc: str = Field(
        default="00:00",
        description="UTC HH:MM time after which this bar kind is persisted.",
    )


class EODHDExchangeConfig(BaseModel):
    """Availability and background persistence settings for one EODHD exchange."""

    daily_bars: EODHDBarScheduleConfig = Field(
        default_factory=EODHDBarScheduleConfig,
    )
    five_min_bars: EODHDBarScheduleConfig = Field(
        default_factory=EODHDBarScheduleConfig,
    )
    target_date_offset_days: int = Field(
        default=0,
        description="Offset from the current UTC date to the market date being finalized. US uses -1 because it finalizes after UTC midnight.",
    )
    enabled: bool = Field(
        default=False,
        description="Whether this exchange is available for live tracking, trading, metadata, corporate actions, and background persistence.",
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_bar_schedules(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        daily_finalize_utc = payload.pop("daily_finalize_utc", None)
        five_min_finalize_utc = payload.pop("five_min_finalize_utc", None)
        if "daily_bars" not in payload and isinstance(daily_finalize_utc, str):
            payload["daily_bars"] = {
                "enabled": True,
                "finalize_utc": daily_finalize_utc,
            }
        if "five_min_bars" not in payload and isinstance(five_min_finalize_utc, str):
            payload["five_min_bars"] = {
                "enabled": True,
                "finalize_utc": five_min_finalize_utc,
            }
        return payload


class EODHDConfig(PersistentMarketDataArenaConfig):
    """EODHD all-in-one market-data and paper-trading arena settings."""

    arena_id: ClassVar[str] = "eodhd"

    api_token: str = Field(
        default="demo",
        description="EODHD API token. The demo token is useful only for smoke checks.",
    )
    websocket_subscribe_limit: int = Field(
        default=50,
        gt=0,
        description="Maximum concurrent symbols subscribed on each EODHD websocket endpoint. Least-recently-used symbols are unsubscribed when the limit is reached.",
    )
    exchanges: dict[str, EODHDExchangeConfig] = Field(
        default_factory=lambda: {
            "US": EODHDExchangeConfig(
                daily_bars=EODHDBarScheduleConfig(
                    enabled=True,
                    finalize_utc="01:30",
                ),
                five_min_bars=EODHDBarScheduleConfig(
                    enabled=True,
                    finalize_utc="02:00",
                ),
                target_date_offset_days=-1,
                enabled=True,
            ),
            "HK": EODHDExchangeConfig(
                daily_bars=EODHDBarScheduleConfig(
                    enabled=True,
                    finalize_utc="09:30",
                ),
                five_min_bars=EODHDBarScheduleConfig(
                    enabled=True,
                    finalize_utc="10:00",
                ),
                target_date_offset_days=0,
                enabled=True,
            ),
        },
        description="Per-EODHD-exchange availability and bar-persistence settings. US and HK are enabled by default.",
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_exchanges(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        configured_exchanges = payload.get("exchanges")
        legacy_schedules = payload.pop("market_schedules", None)
        if configured_exchanges is None and isinstance(legacy_schedules, list):
            migrated: dict[str, object] = {}
            for schedule in legacy_schedules:
                if not isinstance(schedule, dict):
                    continue
                exchange_config = dict(schedule)
                exchange = exchange_config.pop("exchange", None)
                if not isinstance(exchange, str):
                    continue
                normalized_exchange = exchange.strip().upper()
                if normalized_exchange:
                    migrated[normalized_exchange] = exchange_config
            configured_exchanges = migrated
        if isinstance(configured_exchanges, dict):
            normalized: dict[str, object] = {}
            for exchange, exchange_config in configured_exchanges.items():
                if not isinstance(exchange, str):
                    continue
                normalized_exchange = exchange.strip().upper()
                if normalized_exchange:
                    normalized[normalized_exchange] = exchange_config
            payload["exchanges"] = normalized
        return payload

    allowed_currencies: list[str] = Field(
        default_factory=lambda: ["USD"],
        description="Currencies agents may choose in the EODHD paper arena.",
    )
    default_currency: str = Field(
        default="USD",
        description="Currency used when creating an EODHD agent without an explicit currency.",
    )
    fees: EODHDFeeConfig = Field(
        default_factory=EODHDFeeConfig,
        description="Fee schedule applied to EODHD fills.",
    )


class AppConfig(BaseModel):
    """Top-level server configuration. Host/port are uvicorn CLI flags, not config."""

    market_data_root: str = Field(
        default=str(Path.home() / ".quant-arena" / "market-data"),
        description="Global market-data directory. Arenas without an override persist under <market_data_root>/<arena id>.",
    )
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
