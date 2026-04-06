from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from quant_arena.market import StaticMarketDataProvider
from quant_arena.models import QuoteSnapshot
from quant_arena.server import create_app


def _write_json(path: Path, data: dict) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("w", encoding="utf-8") as handle:
		json.dump(data, handle, ensure_ascii=False, indent="\t")
		handle.write("\n")


def _make_app(tmp_path: Path, quote_price: float = 10.0, quote_time: datetime | None = None) -> TestClient:
	quote = QuoteSnapshot(
		symbol="sh.600000",
		name="demo",
		trade_date=date(2026, 4, 6),
		as_of=quote_time or datetime(2026, 4, 6, 1, 0, tzinfo=timezone.utc),
		last_price=quote_price,
		prev_close=10.0,
		limit_up=11.0,
		limit_down=9.0,
	)
	config_path = tmp_path / "config" / "app.json"
	_write_json(
		config_path,
		{
			"project_root": str((tmp_path / "private-project").resolve()),
			"market_data_root": str((tmp_path / "public-market").resolve()),
			"enable_background_polling": False,
			"polling_interval_seconds": 0,
			"fees": {"commission_bps": 3.0, "min_commission": 5.0, "stamp_tax_bps": 10.0},
		},
	)
	app = create_app(config_path=config_path, market_provider=StaticMarketDataProvider({"sh.600000": quote}))
	return TestClient(app)


def test_paths_and_agent_lifecycle(tmp_path: Path) -> None:
	with _make_app(tmp_path) as client:
		paths = client.get("/api/paths")
		assert paths.status_code == 200
		payload = paths.json()
		assert payload["project_root"].endswith("private-project")
		assert payload["market_data_root"].endswith("public-market")

		created = client.post(
			"/api/agents",
			json={
				"agent_id": "alpha",
				"display_name": "Alpha",
				"token_secret": "secret",
				"initial_cash": 100000,
			},
		)
		assert created.status_code == 200

		listed = client.get("/api/agents")
		assert listed.status_code == 200
		assert [item["agent_id"] for item in listed.json()] == ["alpha"]

		agents_path = tmp_path / "private-project" / "config" / "agents.json"
		assert agents_path.exists()
		assert not (tmp_path / "public-market" / "config" / "agents.json").exists()


def test_submit_buy_then_fill_on_next_refresh(tmp_path: Path) -> None:
	initial_quote_time = datetime(2026, 4, 6, 1, 0, tzinfo=timezone.utc)
	with _make_app(tmp_path, quote_price=10.0, quote_time=initial_quote_time) as client:
		client.post(
			"/api/agents",
			json={
				"agent_id": "alpha",
				"display_name": "Alpha",
				"token_secret": "secret",
				"initial_cash": 100000,
			},
		)

		order = client.post(
			"/api/agents/alpha/orders",
			json={"symbol": "sh.600000", "side": "buy", "quantity": 100, "limit_price": 10.0},
		)
		assert order.status_code == 200
		assert order.json()["status"] == "pending"

		portfolio = client.get("/api/agents/alpha/portfolio")
		assert portfolio.status_code == 200
		assert portfolio.json()["pending_orders"][0]["status"] == "pending"

		client.post("/api/market/refresh")
		operations = client.get("/api/agents/alpha/operations")
		assert operations.status_code == 200
		assert operations.json()["fills"] == []

		app = create_app(
			config_path=tmp_path / "config" / "app.json",
			market_provider=StaticMarketDataProvider(
				{
					"sh.600000": QuoteSnapshot(
						symbol="sh.600000",
						name="demo",
						trade_date=date(2026, 4, 7),
						as_of=datetime(2026, 4, 7, 1, 0, tzinfo=timezone.utc),
						last_price=9.8,
						prev_close=10.0,
						limit_up=11.0,
						limit_down=9.0,
					)
				}
			),
		)
		with TestClient(app) as next_client:
			next_client.post("/api/market/refresh")
			operations = next_client.get("/api/agents/alpha/operations")
			assert operations.status_code == 200
			assert len(operations.json()["fills"]) == 1
			portfolio = next_client.get("/api/agents/alpha/portfolio")
			assert portfolio.status_code == 200
			assert portfolio.json()["positions"][0]["quantity"] == 100


def test_t_plus_one_blocks_same_day_sell_until_next_day(tmp_path: Path) -> None:
	with _make_app(tmp_path, quote_price=10.0, quote_time=datetime(2026, 4, 6, 1, 0, tzinfo=timezone.utc)) as client:
		client.post(
			"/api/agents",
			json={
				"agent_id": "alpha",
				"display_name": "Alpha",
				"token_secret": "secret",
				"initial_cash": 100000,
			},
		)
		client.post("/api/agents/alpha/orders", json={"symbol": "sh.600000", "side": "buy", "quantity": 100, "limit_price": 10.0})
		client.post("/api/market/refresh")
		operations = client.get("/api/agents/alpha/operations")
		assert operations.json()["fills"] == []

	app_fill = create_app(
		config_path=tmp_path / "config" / "app.json",
		market_provider=StaticMarketDataProvider(
			{
				"sh.600000": QuoteSnapshot(
					symbol="sh.600000",
					name="demo",
					trade_date=date(2026, 4, 7),
					as_of=datetime(2026, 4, 7, 1, 0, tzinfo=timezone.utc),
					last_price=9.8,
					prev_close=10.0,
					limit_up=11.0,
					limit_down=9.0,
				)
			}
		),
	)
	with TestClient(app_fill) as fill_client:
		fill_client.post("/api/market/refresh")

		sell = fill_client.post(
			"/api/agents/alpha/orders",
			json={"symbol": "sh.600000", "side": "sell", "quantity": 100, "limit_price": 9.8},
		)
		assert sell.status_code == 200
		fill_client.post("/api/market/refresh")

		operations = fill_client.get("/api/agents/alpha/operations")
		assert len(operations.json()["fills"]) == 1

	app_sell = create_app(
		config_path=tmp_path / "config" / "app.json",
		market_provider=StaticMarketDataProvider(
			{
				"sh.600000": QuoteSnapshot(
					symbol="sh.600000",
					name="demo",
					trade_date=date(2026, 4, 8),
					as_of=datetime(2026, 4, 8, 1, 0, tzinfo=timezone.utc),
					last_price=9.8,
					prev_close=9.8,
					limit_up=10.78,
					limit_down=8.82,
				)
			}
		),
	)
	with TestClient(app_sell) as sell_client:
		sell_client.post("/api/market/refresh")
		operations = sell_client.get("/api/agents/alpha/operations")
		assert len(operations.json()["fills"]) == 2


def test_mcp_requires_token_and_can_submit_order(tmp_path: Path) -> None:
	with _make_app(tmp_path) as client:
		client.post(
			"/api/agents",
			json={
				"agent_id": "alpha",
				"display_name": "Alpha",
				"token_secret": "secret",
				"initial_cash": 100000,
			},
		)

		unauthorized = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
		assert unauthorized.status_code == 401

		authorized = client.post(
			"/mcp",
			headers={"X-Agent-Token": "secret"},
			json={
				"jsonrpc": "2.0",
				"id": 2,
				"method": "tools/call",
				"params": {
					"name": "submit_operation",
					"arguments": {
						"symbol": "sh.600000",
						"side": "buy",
						"quantity": 100,
						"limit_price": 10.0,
					},
				},
			},
		)
		assert authorized.status_code == 200
		assert authorized.json()["result"]["content"][0]["json"]["symbol"] == "sh.600000"
