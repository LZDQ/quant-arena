"""Application configuration."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


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
	token_header_name: str = Field(
		default="X-Agent-Token",
		description="Global HTTP header name used for agent REST and MCP authentication.",
	)
	polling_interval_seconds: int = Field(
		default=300,
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
