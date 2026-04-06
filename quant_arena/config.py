"""Application configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class FeeConfig(BaseModel):
	"""Trading fee configuration."""

	commission_bps: float = 3.0
	min_commission: float = 5.0
	stamp_tax_bps: float = 10.0


class AppConfig(BaseModel):
	"""Top-level server configuration."""

	host: str = "127.0.0.1"
	port: int = 18792
	timezone: str = "Asia/Shanghai"
	project_root: str = "./var/project"
	market_data_root: str = "./var/market-data"
	polling_interval_seconds: int = 300
	enable_background_polling: bool = True
	fees: FeeConfig = Field(default_factory=FeeConfig)


class AgentConfig(BaseModel):
	"""One managed trading agent."""

	agent_id: str
	display_name: str
	token_header_name: str = "X-Agent-Token"
	token_secret: str
	initial_cash: float = Field(gt=0)
	sell_constraint: Literal["t_plus_one"] = "t_plus_one"
	active: bool = True


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


def load_agents_config(path: Path) -> list[AgentConfig]:
	"""Load agent config list."""

	if not path.exists():
		path.parent.mkdir(parents=True, exist_ok=True)
		with path.open("w", encoding="utf-8") as handle:
			json.dump([], handle, ensure_ascii=False, indent="\t")
			handle.write("\n")
		return []
	with path.open("r", encoding="utf-8") as handle:
		payload = json.load(handle)
	return [AgentConfig.model_validate(item) for item in payload]
