"""Filesystem persistence bridge."""

import csv
import json
import shutil
from datetime import date, datetime
from pathlib import Path

from quant_arena.clock import SHANGHAI_TZ
from quant_arena.config import AgentConfig
from quant_arena.models import AgentState, CodeNameEntry, DailyBar, FiveMinuteBar


class StorageService:
    """
    Persist private project data separately from market data.

    Since we don't have database, this service aims to provide a bridge between
    logical services and filesystem persistence.
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

    def save_daily_bar_rows(self, bars: list[DailyBar]) -> None:
        self.market_bars_dir.mkdir(parents=True, exist_ok=True)
        bars_by_date: dict[date, dict[str, DailyBar]] = {}
        for bar in bars:
            bars_by_date.setdefault(bar.trade_date, {})[bar.code] = bar

        for trade_date, new_rows in bars_by_date.items():
            existing_bars = self.load_daily_bar(trade_date) or []
            existing = {bar.code: bar for bar in existing_bars}
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

    def save_five_minute_bar_rows(self, bars: list[FiveMinuteBar]) -> None:
        self.market_bars_dir.mkdir(parents=True, exist_ok=True)
        bars_by_partition: dict[tuple[date, str], dict[str, FiveMinuteBar]] = {}
        for bar in bars:
            minute = bar.bar_time.strftime("%H-%M")
            bars_by_partition.setdefault((bar.trade_date, minute), {})[bar.code] = bar

        for (trade_date, minute), new_rows in bars_by_partition.items():
            existing_bars = self.load_five_minute_bars(trade_date) or []
            existing = {
                bar.code: bar
                for bar in existing_bars
                if bar.bar_time.strftime("%H-%M") == minute
            }
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
        if not entries:
            return
        self.market_data_root.mkdir(parents=True, exist_ok=True)
        with self.market_codes_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "code",
                    "name",
                ],
            )
            writer.writeheader()
            for entry in sorted(entries, key=lambda item: item.code):
                writer.writerow(
                    {
                        "code": entry.code,
                        "name": entry.name,
                    }
                )

    def load_code_names(self) -> list[CodeNameEntry] | None:
        if not self.market_codes_path.exists():
            return None
        with self.market_codes_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [
                CodeNameEntry(
                    code=row["code"],
                    name=row["name"],
                )
                for row in reader
            ]

    def code_names_last_refreshed_at(self) -> datetime | None:
        if not self.market_codes_path.exists():
            return None
        return datetime.fromtimestamp(self.market_codes_path.stat().st_mtime, tz=SHANGHAI_TZ)

    def load_daily_bar(self, trade_date: date) -> list[DailyBar] | None:
        path = self.market_bars_dir / trade_date.isoformat() / "daily.csv"
        if not path.exists():
            return None
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

    def load_five_minute_bars(self, trade_date: date) -> list[FiveMinuteBar] | None:
        date_dir = self.market_bars_dir / trade_date.isoformat() / "5min"
        if not date_dir.exists():
            return None
        bars: list[FiveMinuteBar] = []
        for path in sorted(date_dir.glob("*.csv")):
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
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
