import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from mcp import types

from quant_arena.models import CodeNameEntry, DailyBar, FiveMinuteBar, QuoteSnapshot
from quant_arena.server import create_app
from tests.support_market import StaticMarketDataProvider


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent="\t")
        handle.write("\n")


def _make_app(
    tmp_path: Path,
    quote_price: float = 10.0,
    quote_time: datetime | None = None,
    code_names: list[CodeNameEntry] | None = None,
    daily_bars: dict[tuple[str, date], DailyBar] | None = None,
    five_minute_bars: dict[tuple[str, date], list[FiveMinuteBar]] | None = None,
) -> TestClient:
    resolved_quote_time = quote_time or datetime(2026, 4, 6, 1, 0, tzinfo=timezone.utc)
    quote = QuoteSnapshot(
        code="sh.600000",
        name="demo",
        trade_date=resolved_quote_time.date(),
        as_of=resolved_quote_time,
        last_price=quote_price,
        prev_close=10.0,
        limit_up=11.0,
        limit_down=9.0,
    )
    config_path = tmp_path / "config" / "app.json"
    _write_json(
        config_path,
        {
            "agents_root": str((tmp_path / "private-agents").resolve()),
            "market_data_root": str((tmp_path / "public-market").resolve()),
            "enable_background_polling": False,
            "enable_code_name_refresh": False,
            "polling_interval_seconds": 0,
            "fees": {"commission_bps": 3.0, "min_commission": 5.0, "stamp_tax_bps": 10.0},
        },
    )
    app = create_app(
        config_path=config_path,
        market_provider=StaticMarketDataProvider(
            {"sh.600000": quote},
            code_names=code_names,
            daily_bars=daily_bars,
            five_minute_bars=five_minute_bars,
        ),
    )
    return TestClient(app)


def test_paths_and_agent_lifecycle(tmp_path: Path) -> None:
    with _make_app(tmp_path) as client:
        paths = client.get("/api/paths")
        assert paths.status_code == 200
        payload = paths.json()
        assert payload["agents_root"].endswith("private-agents")
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

        agent_config_path = tmp_path / "private-agents" / "alpha" / "config.json"
        assert agent_config_path.exists()
        with agent_config_path.open("r", encoding="utf-8") as handle:
            agent_config_payload = json.load(handle)
        assert "agent_id" not in agent_config_payload
        assert not (tmp_path / "public-market" / "alpha" / "config.json").exists()


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
            json={"code": "sh.600000", "side": "buy", "quantity": 100, "limit_price": 10.0},
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
                        code="sh.600000",
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
        client.post("/api/agents/alpha/orders", json={"code": "sh.600000", "side": "buy", "quantity": 100, "limit_price": 10.0})
        client.post("/api/market/refresh")
        operations = client.get("/api/agents/alpha/operations")
        assert operations.json()["fills"] == []

    app_fill = create_app(
        config_path=tmp_path / "config" / "app.json",
        market_provider=StaticMarketDataProvider(
            {
                "sh.600000": QuoteSnapshot(
                    code="sh.600000",
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
            json={"code": "sh.600000", "side": "sell", "quantity": 100, "limit_price": 9.8},
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
                    code="sh.600000",
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

        initialized = client.post(
            "/mcp",
            headers={"X-Agent-Token": "secret"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": types.LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0.1.0"},
                },
            },
        )
        assert initialized.status_code == 200
        assert initialized.json()["result"]["serverInfo"]["name"] == "quant-arena"

        tools = client.post(
            "/mcp",
            headers={"X-Agent-Token": "secret"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert tools.status_code == 200
        assert any(tool["name"] == "submit_operation" for tool in tools.json()["result"]["tools"])


def test_sync_market_data_writes_daily_bar_after_close(tmp_path: Path) -> None:
    trade_date = date(2026, 4, 8)
    with _make_app(
        tmp_path,
        quote_price=10.0,
        quote_time=datetime(2026, 4, 8, 7, 10, tzinfo=timezone.utc),
        daily_bars={
            ("sh.600000", trade_date): DailyBar(
                code="sh.600000",
                trade_date=trade_date,
                open_price=9.9,
                high_price=10.2,
                low_price=9.8,
                close_price=10.0,
                prev_close=9.8,
                volume=123456,
                amount=1234567,
            )
        },
    ) as client:
        client.post(
            "/api/agents",
            json={
                "agent_id": "alpha",
                "display_name": "Alpha",
                "token_secret": "secret",
                "initial_cash": 100000,
            },
        )
        client.post(
            "/api/agents/alpha/orders",
            json={"code": "sh.600000", "side": "buy", "quantity": 100, "limit_price": 10.0},
        )

        client.app.state.ctx.market.sync_market_data(["sh.600000"], now=datetime(2026, 4, 8, 7, 10, tzinfo=timezone.utc))

        daily_path = tmp_path / "public-market" / "bars" / "2026-04-08" / "daily.csv"
        assert daily_path.exists()
        with daily_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[0]["close_price"] == "10.0"


def test_sync_market_data_writes_five_minute_bars_during_session(tmp_path: Path) -> None:
    trade_date = date(2026, 4, 8)
    with _make_app(
        tmp_path,
        quote_price=10.0,
        quote_time=datetime(2026, 4, 8, 2, 5, tzinfo=timezone.utc),
        five_minute_bars={
            ("sh.600000", trade_date): [
                FiveMinuteBar(
                    code="sh.600000",
                    trade_date=trade_date,
                    bar_time=datetime(2026, 4, 8, 10, 0, tzinfo=timezone.utc),
                    open_price=9.95,
                    high_price=10.05,
                    low_price=9.94,
                    close_price=10.0,
                    volume=1000,
                    amount=10000,
                ),
                FiveMinuteBar(
                    code="sh.600000",
                    trade_date=trade_date,
                    bar_time=datetime(2026, 4, 8, 10, 5, tzinfo=timezone.utc),
                    open_price=10.0,
                    high_price=10.08,
                    low_price=9.99,
                    close_price=10.02,
                    volume=900,
                    amount=9018,
                ),
            ]
        },
    ) as client:
        client.post(
            "/api/agents",
            json={
                "agent_id": "alpha",
                "display_name": "Alpha",
                "token_secret": "secret",
                "initial_cash": 100000,
            },
        )
        client.post(
            "/api/agents/alpha/orders",
            json={"code": "sh.600000", "side": "buy", "quantity": 100, "limit_price": 10.0},
        )

        client.app.state.ctx.market.sync_market_data(["sh.600000"], now=datetime(2026, 4, 8, 2, 5, tzinfo=timezone.utc))

        first_bar_path = tmp_path / "public-market" / "bars" / "2026-04-08" / "5min" / "10-00.csv"
        second_bar_path = tmp_path / "public-market" / "bars" / "2026-04-08" / "5min" / "10-05.csv"
        assert first_bar_path.exists()
        assert second_bar_path.exists()
        with second_bar_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        assert rows[0]["close_price"] == "10.02"

        status = client.get("/api/market/status")
        assert status.status_code == 200
        code_status = status.json()["codes"][0]
        assert code_status["code"] == "sh.600000"
        assert code_status["latest_five_minute_bar_date"] == "2026-04-08"
        assert code_status["five_minute_bar_count"] == 2

        bars = client.get("/api/market/bars", params={"code": "sh.600000"})
        assert bars.status_code == 200
        payload = bars.json()
        assert payload["code"] == "sh.600000"
        assert payload["trade_date"] == "2026-04-08"
        assert len(payload["five_minute_bars"]) == 2
        assert payload["five_minute_bars"][-1]["close_price"] == 10.02


def test_parse_today_market_data_if_missing(tmp_path: Path) -> None:
    trade_date = date(2026, 4, 9)
    with _make_app(
        tmp_path,
        quote_price=10.0,
        quote_time=datetime(2026, 4, 9, 2, 5, tzinfo=timezone.utc),
        daily_bars={
            ("sh.600000", trade_date): DailyBar(
                code="sh.600000",
                trade_date=trade_date,
                open_price=9.9,
                high_price=10.2,
                low_price=9.8,
                close_price=10.0,
                prev_close=9.8,
                volume=123456,
                amount=1234567,
            )
        },
        five_minute_bars={
            ("sh.600000", trade_date): [
                FiveMinuteBar(
                    code="sh.600000",
                    trade_date=trade_date,
                    bar_time=datetime(2026, 4, 8, 10, 0, tzinfo=timezone.utc),
                    open_price=9.95,
                    high_price=10.05,
                    low_price=9.94,
                    close_price=10.0,
                    volume=1000,
                    amount=10000,
                )
            ]
        },
    ) as client:
        client.post(
            "/api/agents",
            json={
                "agent_id": "alpha",
                "display_name": "Alpha",
                "token_secret": "secret",
                "initial_cash": 100000,
            },
        )
        client.post(
            "/api/agents/alpha/orders",
            json={"code": "sh.600000", "side": "buy", "quantity": 100, "limit_price": 10.0},
        )

        result = client.post("/api/market/parse-today")
        assert result.status_code == 200
        payload = result.json()
        assert payload["trade_date"] == "2026-04-09"
        assert payload["tracked_codes"] == ["sh.600000"]
        assert payload["parsed_daily_codes"] == ["sh.600000"]
        assert payload["parsed_five_minute_codes"] == ["sh.600000"]

        daily_path = tmp_path / "public-market" / "bars" / "2026-04-09" / "daily.csv"
        five_minute_path = tmp_path / "public-market" / "bars" / "2026-04-09" / "5min" / "10-00.csv"
        assert daily_path.exists()
        assert five_minute_path.exists()

        again = client.post("/api/market/parse-today")
        assert again.status_code == 200
        assert again.json()["parsed_daily_codes"] == []
        assert again.json()["parsed_five_minute_codes"] == []


def test_refresh_and_search_code_names_with_paging(tmp_path: Path) -> None:
    code_names = [
        CodeNameEntry(code=f"sh.6000{i:02d}", name=f"Name {i}", trade_status="1")
        for i in range(25)
    ]
    with _make_app(tmp_path, code_names=code_names) as client:
        result = client.post("/api/market/codes/refresh")
        assert result.status_code == 200
        assert result.json()["entry_count"] == 25

        codes_path = tmp_path / "public-market" / "codes.csv"
        assert codes_path.exists()

        first_page = client.get("/api/market/codes", params={"page": 1, "page_size": 20})
        assert first_page.status_code == 200
        first_payload = first_page.json()
        assert first_payload["total"] == 25
        assert len(first_payload["items"]) == 20
        assert first_payload["auto_refresh_enabled"] is False

        second_page = client.get("/api/market/codes", params={"page": 2, "page_size": 20})
        assert second_page.status_code == 200
        assert len(second_page.json()["items"]) == 5

        filtered = client.get("/api/market/codes", params={"query": "Name 2", "page": 1, "page_size": 20})
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["total"] == 6
        assert filtered_payload["items"][0]["code"] == "sh.600002"
