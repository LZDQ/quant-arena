"""Filesystem persistence."""

import json
from datetime import date
from pathlib import Path

from quant_arena.config import AgentConfig
from quant_arena.models import AgentState, DailyBar, FiveMinuteBar


class ArenaStorage:
	"""Persist private project data separately from market data."""

	def __init__(self, agents_root: Path, market_data_root: Path):
		self.agents_root = agents_root
		self.market_data_root = market_data_root
		self.agent_dir = self.agents_root
		self.market_daily_bars_dir = self.market_data_root / "daily-bars"
		self.market_five_minute_bars_dir = self.market_data_root / "5min-bars"

	def ensure_layout(self) -> None:
		self.agent_dir.mkdir(parents=True, exist_ok=True)
		self.market_daily_bars_dir.mkdir(parents=True, exist_ok=True)
		self.market_five_minute_bars_dir.mkdir(parents=True, exist_ok=True)

	def agent_root(self, agent_id: str) -> Path:
		return self.agent_dir / agent_id

	def agent_config_path(self, agent_id: str) -> Path:
		return self.agent_root(agent_id) / "config.json"

	def save_agent_config(self, agent: AgentConfig) -> None:
		path = self.agent_config_path(agent.agent_id)
		path.parent.mkdir(parents=True, exist_ok=True)
		with path.open("w", encoding="utf-8") as handle:
			json.dump(agent.model_dump(mode="json"), handle, ensure_ascii=False, indent="\t")
			handle.write("\n")

	def load_agent_config(self, agent_id: str) -> AgentConfig | None:
		path = self.agent_config_path(agent_id)
		if not path.exists():
			return None
		with path.open("r", encoding="utf-8") as handle:
			return AgentConfig.model_validate(json.load(handle))

	def load_agent_configs(self) -> list[AgentConfig]:
		if not self.agent_dir.exists():
			return []
		agents: list[AgentConfig] = []
		for path in sorted(self.agent_dir.iterdir(), key=lambda item: item.name):
			if not path.is_dir():
				continue
			config = self.load_agent_config(path.name)
			if config is not None:
				agents.append(config)
		return agents

	def load_agent_state(self, agent_id: str, initial_cash: float) -> AgentState:
		path = self.agent_dir / agent_id / "state.json"
		if not path.exists():
			return AgentState(agent_id=agent_id, cash=initial_cash)
		with path.open("r", encoding="utf-8") as handle:
			return AgentState.model_validate(json.load(handle))

	def save_agent_state(self, state: AgentState) -> None:
		path = self.agent_dir / state.agent_id / "state.json"
		path.parent.mkdir(parents=True, exist_ok=True)
		with path.open("w", encoding="utf-8") as handle:
			json.dump(state.model_dump(mode="json"), handle, ensure_ascii=False, indent="\t")
			handle.write("\n")

	def delete_agent_state(self, agent_id: str) -> None:
		agent_root = self.agent_root(agent_id)
		for child in ("config.json", "state.json"):
			path = agent_root / child
			if path.exists():
				path.unlink()
		if agent_root.exists():
			agent_root.rmdir()

	def save_daily_bars(self, bars_by_code: dict[str, DailyBar]) -> None:
		self.market_daily_bars_dir.mkdir(parents=True, exist_ok=True)
		for code, bar in bars_by_code.items():
			path = self.market_daily_bars_dir / code / f"{bar.trade_date.isoformat()}.json"
			path.parent.mkdir(parents=True, exist_ok=True)
			with path.open("w", encoding="utf-8") as handle:
				json.dump(bar.model_dump(mode="json"), handle, ensure_ascii=False, indent="\t")
				handle.write("\n")

	def save_five_minute_bars(self, bars_by_code: dict[str, list[FiveMinuteBar]]) -> None:
		self.market_five_minute_bars_dir.mkdir(parents=True, exist_ok=True)
		for code, bars in bars_by_code.items():
			if not bars:
				continue
			trade_date = bars[0].trade_date
			path = self.market_five_minute_bars_dir / code / f"{trade_date.isoformat()}.json"
			path.parent.mkdir(parents=True, exist_ok=True)
			with path.open("w", encoding="utf-8") as handle:
				json.dump([bar.model_dump(mode="json") for bar in bars], handle, ensure_ascii=False, indent="\t")
				handle.write("\n")

	def load_daily_bar(self, code: str, trade_date: date) -> DailyBar | None:
		path = self.market_daily_bars_dir / code / f"{trade_date.isoformat()}.json"
		if not path.exists():
			return None
		with path.open("r", encoding="utf-8") as handle:
			return DailyBar.model_validate(json.load(handle))

	def load_five_minute_bars(self, code: str, trade_date: date) -> list[FiveMinuteBar]:
		path = self.market_five_minute_bars_dir / code / f"{trade_date.isoformat()}.json"
		if not path.exists():
			return []
		with path.open("r", encoding="utf-8") as handle:
			payload = json.load(handle)
		return [FiveMinuteBar.model_validate(item) for item in payload]

	def list_market_codes(self) -> list[str]:
		codes: set[str] = set()
		if self.market_daily_bars_dir.exists():
			codes.update(path.name for path in self.market_daily_bars_dir.iterdir() if path.is_dir())
		if self.market_five_minute_bars_dir.exists():
			codes.update(path.name for path in self.market_five_minute_bars_dir.iterdir() if path.is_dir())
		return sorted(codes)

	def latest_daily_bar_date(self, code: str) -> date | None:
		code_dir = self.market_daily_bars_dir / code
		if not code_dir.exists():
			return None
		candidates = sorted(path.stem for path in code_dir.glob("*.json"))
		if not candidates:
			return None
		return date.fromisoformat(candidates[-1])

	def latest_five_minute_bar_date(self, code: str) -> date | None:
		code_dir = self.market_five_minute_bars_dir / code
		if not code_dir.exists():
			return None
		candidates = sorted(path.stem for path in code_dir.glob("*.json"))
		if not candidates:
			return None
		return date.fromisoformat(candidates[-1])
