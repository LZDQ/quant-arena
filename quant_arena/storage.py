"""Filesystem persistence."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from quant_arena.config import AgentConfig
from quant_arena.models import AgentState, QuoteSnapshot


class ArenaStorage:
	"""Persist private project data separately from market data."""

	def __init__(self, project_root: Path, market_data_root: Path):
		self.project_root = project_root
		self.market_data_root = market_data_root
		self.config_dir = self.project_root / "config"
		self.agent_dir = self.project_root / "agents"
		self.market_quotes_dir = self.market_data_root / "quotes"
		self.market_bars_dir = self.market_data_root / "daily-bars"
		self.market_calendar_dir = self.market_data_root / "calendar"

	def ensure_layout(self) -> None:
		self.config_dir.mkdir(parents=True, exist_ok=True)
		self.agent_dir.mkdir(parents=True, exist_ok=True)
		self.market_quotes_dir.mkdir(parents=True, exist_ok=True)
		self.market_bars_dir.mkdir(parents=True, exist_ok=True)
		self.market_calendar_dir.mkdir(parents=True, exist_ok=True)

	def agents_config_path(self) -> Path:
		return self.config_dir / "agents.json"

	def save_agents(self, agents: list[AgentConfig]) -> None:
		self.config_dir.mkdir(parents=True, exist_ok=True)
		with self.agents_config_path().open("w", encoding="utf-8") as handle:
			json.dump([agent.model_dump(mode="json") for agent in agents], handle, ensure_ascii=False, indent="\t")
			handle.write("\n")

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
		path = self.agent_dir / agent_id / "state.json"
		if path.exists():
			path.unlink()
		agent_root = path.parent
		if agent_root.exists():
			agent_root.rmdir()

	def save_quotes(self, quotes: dict[str, QuoteSnapshot]) -> None:
		self.market_quotes_dir.mkdir(parents=True, exist_ok=True)
		for symbol, quote in quotes.items():
			path = self.market_quotes_dir / f"{symbol}.json"
			with path.open("w", encoding="utf-8") as handle:
				json.dump(quote.model_dump(mode="json"), handle, ensure_ascii=False, indent="\t")
				handle.write("\n")

	def load_quote(self, symbol: str) -> QuoteSnapshot | None:
		path = self.market_quotes_dir / f"{symbol}.json"
		if not path.exists():
			return None
		with path.open("r", encoding="utf-8") as handle:
			return QuoteSnapshot.model_validate(json.load(handle))

	def save_trading_day(self, day: date) -> None:
		self.market_calendar_dir.mkdir(parents=True, exist_ok=True)
		path = self.market_calendar_dir / f"{day.isoformat()}.json"
		with path.open("w", encoding="utf-8") as handle:
			json.dump({"trade_date": day.isoformat()}, handle, ensure_ascii=False, indent="\t")
			handle.write("\n")
