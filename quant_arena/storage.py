"""Filesystem persistence bridge."""

import csv
import json
import shutil
from datetime import date, datetime
from pathlib import Path

from quant_arena.clock import SHANGHAI_TZ
from quant_arena.config import AgentConfig
from quant_arena.models import AgentState


class StorageService:
    """
    Persist private project data separately from market data.
    """

    def __init__(self, agents_root: Path, market_data_root: Path):
        self.agents_root = agents_root
        self.market_data_root = market_data_root
        self.market_bars_dir = self.market_data_root / "bars"
        self.market_codes_path = self.market_data_root / "codes.csv"

    def ensure_layout(self) -> None:
        self.agents_root.mkdir(parents=True, exist_ok=True)
        self.market_bars_dir.mkdir(parents=True, exist_ok=True)

    def agent_root(self, agent_id: str) -> Path:
        return self.agents_root / agent_id

    def agent_config_path(self, agent_id: str) -> Path:
        return self.agent_root(agent_id) / "config.json"

    def agent_state_path(self, agent_id: str) -> Path:
        return self.agent_root(agent_id) / "state.json"

    def save_agent_config(self, agent_id: str, agent: AgentConfig) -> None:
        path = self.agent_config_path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            payload = agent.model_dump(mode="json")
            json.dump(payload, handle, ensure_ascii=False, indent="\t")

    def load_agent_config(self, agent_id: str) -> AgentConfig | None:
        path = self.agent_config_path(agent_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return AgentConfig.model_validate(json.load(handle))

    def load_agent_configs(self) -> dict[str, AgentConfig]:
        if not self.agents_root.exists():
            return {}
        agents: dict[str, AgentConfig] = {}
        for path in sorted(self.agents_root.iterdir(), key=lambda item: item.name):
            if not path.is_dir():
                continue
            config = self.load_agent_config(path.name)
            if config:
                agents[path.name] = config
        return agents

    def load_agent_state(self, agent_id: str) -> AgentState | None:
        path = self.agent_state_path(agent_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return AgentState.model_validate(json.load(handle))

    def save_agent_state(self, state: AgentState) -> None:
        path = self.agent_state_path(state.agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(state.model_dump(mode="json"), handle, ensure_ascii=False, indent="\t")

    def delete_agent_dir(self, agent_id: str) -> None:
        agent_root = self.agent_root(agent_id)
        if agent_root.exists():
            shutil.rmtree(agent_root)
