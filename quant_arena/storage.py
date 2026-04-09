"""Filesystem persistence."""

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

from quant_arena.config import AgentConfig
from quant_arena.models import AgentState, CodeNameEntry, DailyBar, FiveMinuteBar


class ArenaStorage:
	"""Persist private project data separately from market data."""

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

	def save_agent_config(self, agent_id: str, agent: AgentConfig) -> None:
		path = self.agent_config_path(agent_id)
		path.parent.mkdir(parents=True, exist_ok=True)
		with path.open("w", encoding="utf-8") as handle:
			payload = agent.model_dump(mode="json")
			json.dump(payload, handle, ensure_ascii=False, indent="\t")
			handle.write("\n")

	def load_agent_config(self, agent_id: str) -> AgentConfig | None:
		path = self.agent_config_path(agent_id)
		if not path.exists():
			return None
		with path.open("r", encoding="utf-8") as handle:
			payload = json.load(handle)
		return AgentConfig.model_validate(payload)

	def load_agent_configs(self) -> dict[str, AgentConfig]:
		if not self.agents_root.exists():
			return {}
		agents: dict[str, AgentConfig] = {}
		for path in sorted(self.agents_root.iterdir(), key=lambda item: item.name):
			if not path.is_dir():
				continue
			config = self.load_agent_config(path.name)
			if config is not None:
				agents[path.name] = config
		return agents

	def load_agent_state(self, agent_id: str, initial_cash: float) -> AgentState:
		path = self.agents_root / agent_id / "state.json"
		if not path.exists():
			return AgentState(agent_id=agent_id, cash=initial_cash)
		with path.open("r", encoding="utf-8") as handle:
			return AgentState.model_validate(json.load(handle))

	def save_agent_state(self, state: AgentState) -> None:
		path = self.agents_root / state.agent_id / "state.json"
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
		self.market_bars_dir.mkdir(parents=True, exist_ok=True)
		bars_by_date: dict[date, dict[str, DailyBar]] = {}
		for code, bar in bars_by_code.items():
			bars_by_date.setdefault(bar.trade_date, {})[code] = bar

		for trade_date, new_rows in bars_by_date.items():
			existing = {bar.code: bar for bar in self._load_all_daily_bars(trade_date)}
			existing.update(new_rows)
			path = self.market_bars_dir / trade_date.isoformat() / "daily.csv"
			path.parent.mkdir(parents=True, exist_ok=True)
			with path.open("w", encoding="utf-8", newline="") as handle:
				writer = csv.DictWriter(
					handle,
					fieldnames=[
						"code",
						"trade_date",
						"open_price",
						"high_price",
						"low_price",
						"close_price",
						"prev_close",
						"volume",
						"amount",
					],
				)
				writer.writeheader()
				for code in sorted(existing):
					bar = existing[code]
					writer.writerow(
						{
							"code": bar.code,
							"trade_date": bar.trade_date.isoformat(),
							"open_price": bar.open_price,
							"high_price": bar.high_price,
							"low_price": bar.low_price,
							"close_price": bar.close_price,
							"prev_close": bar.prev_close,
							"volume": bar.volume,
							"amount": bar.amount,
						}
					)

	def save_five_minute_bars(self, bars_by_code: dict[str, list[FiveMinuteBar]]) -> None:
		self.market_bars_dir.mkdir(parents=True, exist_ok=True)
		bars_by_partition: dict[tuple[date, str], dict[str, FiveMinuteBar]] = {}
		for code, bars in bars_by_code.items():
			for bar in bars:
				minute = bar.bar_time.strftime("%H-%M")
				bars_by_partition.setdefault((bar.trade_date, minute), {})[code] = bar

		for (trade_date, minute), new_rows in bars_by_partition.items():
			existing = {bar.code: bar for bar in self._load_minute_bars(trade_date, minute)}
			existing.update(new_rows)
			path = self.market_bars_dir / trade_date.isoformat() / "5min" / f"{minute}.csv"
			path.parent.mkdir(parents=True, exist_ok=True)
			with path.open("w", encoding="utf-8", newline="") as handle:
				writer = csv.DictWriter(
					handle,
					fieldnames=[
						"code",
						"trade_date",
						"bar_time",
						"open_price",
						"high_price",
						"low_price",
						"close_price",
						"volume",
						"amount",
					],
				)
				writer.writeheader()
				for code in sorted(existing):
					bar = existing[code]
					writer.writerow(
						{
							"code": bar.code,
							"trade_date": bar.trade_date.isoformat(),
							"bar_time": bar.bar_time.isoformat(),
							"open_price": bar.open_price,
							"high_price": bar.high_price,
							"low_price": bar.low_price,
							"close_price": bar.close_price,
							"volume": bar.volume,
							"amount": bar.amount,
						}
					)

	def save_code_names(self, entries: list[CodeNameEntry]) -> None:
		self.market_data_root.mkdir(parents=True, exist_ok=True)
		with self.market_codes_path.open("w", encoding="utf-8", newline="") as handle:
			writer = csv.DictWriter(
				handle,
				fieldnames=[
					"code",
					"name",
					"trade_status",
				],
			)
			writer.writeheader()
			for entry in sorted(entries, key=lambda item: item.code):
				writer.writerow(
					{
						"code": entry.code,
						"name": entry.name or "",
						"trade_status": entry.trade_status or "",
					}
				)

	def search_code_names(self, query: str, page: int, page_size: int) -> tuple[int, list[CodeNameEntry]]:
		items = self._load_all_code_names()
		needle = query.strip().lower()
		if needle:
			items = [
				item
				for item in items
				if needle in item.code.lower() or needle in (item.name or "").lower()
			]
		total = len(items)
		start = max(page - 1, 0) * page_size
		end = start + page_size
		return total, items[start:end]

	def load_code_name(self, code: str) -> CodeNameEntry | None:
		for entry in self._load_all_code_names():
			if entry.code == code:
				return entry
		return None

	def code_names_last_refreshed_at(self) -> datetime | None:
		if not self.market_codes_path.exists():
			return None
		return datetime.fromtimestamp(self.market_codes_path.stat().st_mtime, tz=timezone.utc)

	def load_daily_bar(self, code: str, trade_date: date) -> DailyBar | None:
		for bar in self._load_all_daily_bars(trade_date):
			if bar.code == code:
				return bar
		return None

	def load_five_minute_bars(self, code: str, trade_date: date) -> list[FiveMinuteBar]:
		date_dir = self.market_bars_dir / trade_date.isoformat() / "5min"
		if not date_dir.exists():
			return []
		bars: list[FiveMinuteBar] = []
		for path in sorted(date_dir.glob("*.csv")):
			with path.open("r", encoding="utf-8", newline="") as handle:
				reader = csv.DictReader(handle)
				for row in reader:
					if row["code"] != code:
						continue
					bars.append(
						FiveMinuteBar(
							code=row["code"],
							trade_date=date.fromisoformat(row["trade_date"]),
							bar_time=datetime.fromisoformat(row["bar_time"]),
							open_price=float(row["open_price"]),
							high_price=float(row["high_price"]),
							low_price=float(row["low_price"]),
							close_price=float(row["close_price"]),
							volume=float(row["volume"]),
							amount=float(row["amount"]),
						)
					)
		return sorted(bars, key=lambda bar: bar.bar_time)

	def list_market_codes(self) -> list[str]:
		codes: set[str] = set()
		for date_dir in sorted(self.market_bars_dir.iterdir()) if self.market_bars_dir.exists() else []:
			if not date_dir.is_dir():
				continue
			codes.update(bar.code for bar in self._load_all_daily_bars(date.fromisoformat(date_dir.name)))
		for date_dir in sorted(self.market_bars_dir.iterdir()) if self.market_bars_dir.exists() else []:
			if not date_dir.is_dir():
				continue
			minute_dir = date_dir / "5min"
			if not minute_dir.exists():
				continue
			for path in sorted(minute_dir.glob("*.csv")):
				with path.open("r", encoding="utf-8", newline="") as handle:
					reader = csv.DictReader(handle)
					codes.update(row["code"] for row in reader)
		return sorted(codes)

	def latest_daily_bar_date(self, code: str) -> date | None:
		candidates: list[date] = []
		for date_dir in sorted(self.market_bars_dir.iterdir()) if self.market_bars_dir.exists() else []:
			if not date_dir.is_dir():
				continue
			trade_date = date.fromisoformat(date_dir.name)
			if self.load_daily_bar(code, trade_date) is not None:
				candidates.append(trade_date)
		return candidates[-1] if candidates else None

	def latest_five_minute_bar_date(self, code: str) -> date | None:
		candidates: list[date] = []
		for date_dir in sorted(self.market_bars_dir.iterdir()) if self.market_bars_dir.exists() else []:
			if not date_dir.is_dir():
				continue
			trade_date = date.fromisoformat(date_dir.name)
			if self.load_five_minute_bars(code, trade_date):
				candidates.append(trade_date)
		return candidates[-1] if candidates else None

	def _load_all_daily_bars(self, trade_date: date) -> list[DailyBar]:
		path = self.market_bars_dir / trade_date.isoformat() / "daily.csv"
		if not path.exists():
			return []
		with path.open("r", encoding="utf-8", newline="") as handle:
			reader = csv.DictReader(handle)
			return [
				DailyBar(
					code=row["code"],
					trade_date=date.fromisoformat(row["trade_date"]),
					open_price=float(row["open_price"]),
					high_price=float(row["high_price"]),
					low_price=float(row["low_price"]),
					close_price=float(row["close_price"]),
					prev_close=float(row["prev_close"]),
					volume=float(row["volume"]),
					amount=float(row["amount"]),
				)
				for row in reader
			]

	def _load_minute_bars(self, trade_date: date, minute: str) -> list[FiveMinuteBar]:
		path = self.market_bars_dir / trade_date.isoformat() / "5min" / f"{minute}.csv"
		if not path.exists():
			return []
		with path.open("r", encoding="utf-8", newline="") as handle:
			reader = csv.DictReader(handle)
			return [
				FiveMinuteBar(
					code=row["code"],
					trade_date=date.fromisoformat(row["trade_date"]),
					bar_time=datetime.fromisoformat(row["bar_time"]),
					open_price=float(row["open_price"]),
					high_price=float(row["high_price"]),
					low_price=float(row["low_price"]),
					close_price=float(row["close_price"]),
					volume=float(row["volume"]),
					amount=float(row["amount"]),
				)
				for row in reader
			]

	def _load_all_code_names(self) -> list[CodeNameEntry]:
		if not self.market_codes_path.exists():
			return []
		with self.market_codes_path.open("r", encoding="utf-8", newline="") as handle:
			reader = csv.DictReader(handle)
			return [
				CodeNameEntry(
					code=row["code"],
					name=row["name"] or None,
					trade_status=row["trade_status"] or None,
				)
				for row in reader
			]
