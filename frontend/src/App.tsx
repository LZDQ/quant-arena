import { FormEvent, useEffect, useState } from "react";

type PathsResponse = {
  config_path: string;
  agents_root: string;
  market_data_root: string;
};

type Agent = {
  agent_id: string;
  display_name: string;
  token_header_name: string;
  token_secret: string;
  initial_cash: number;
  sell_constraint: "t_plus_one";
  active: boolean;
};

type RankingEntry = {
  date: string;
  agent_id: string;
  display_name: string;
  total_equity: number;
  return_pct: number;
  realized_pnl: number;
  unrealized_pnl: number;
};

type MarketCodeStatus = {
  code: string;
  latest_daily_bar_date: string | null;
  latest_five_minute_bar_date: string | null;
  five_minute_bar_count: number;
  last_five_minute_bar_time: string | null;
};

type MarketStatusResponse = {
  tracked_codes: string[];
  codes: MarketCodeStatus[];
};

type DailyBar = {
  code: string;
  trade_date: string;
  open_price: number;
  high_price: number;
  low_price: number;
  close_price: number;
  prev_close: number;
  volume: number;
  amount: number;
};

type FiveMinuteBar = {
  code: string;
  trade_date: string;
  bar_time: string;
  open_price: number;
  high_price: number;
  low_price: number;
  close_price: number;
  volume: number;
  amount: number;
};

type MarketBarsResponse = {
  code: string;
  trade_date: string;
  daily_bar: DailyBar | null;
  five_minute_bars: FiveMinuteBar[];
};

type MarketParseResponse = {
  trade_date: string;
  tracked_codes: string[];
  parsed_daily_codes: string[];
  parsed_five_minute_codes: string[];
};

type AgentDraft = {
  agent_id: string;
  display_name: string;
  token_secret: string;
  initial_cash: string;
};

const EMPTY_DRAFT: AgentDraft = {
  agent_id: "",
  display_name: "",
  token_secret: "",
  initial_cash: "",
};

function getApiBase(): string {
  return (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";
}

function apiUrl(path: string): string {
  return `${getApiBase()}${path}`;
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 2,
  }).format(value);
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 2,
  }).format(value);
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return "None";
  }
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

export default function App() {
  const [paths, setPaths] = useState<PathsResponse | null>(null);
  const [rankings, setRankings] = useState<RankingEntry[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [marketStatus, setMarketStatus] = useState<MarketStatusResponse | null>(null);
  const [selectedCode, setSelectedCode] = useState<string>("");
  const [marketBars, setMarketBars] = useState<MarketBarsResponse | null>(null);
  const [lastParse, setLastParse] = useState<MarketParseResponse | null>(null);
  const [draft, setDraft] = useState<AgentDraft>(EMPTY_DRAFT);
  const [error, setError] = useState<string>("");
  const [isSaving, setIsSaving] = useState(false);
  const [isParsingToday, setIsParsingToday] = useState(false);
  const rankingDate = rankings[0]?.date ?? null;

  async function load(): Promise<void> {
    const [nextPaths, nextRankings, nextAgents, nextMarketStatus] = await Promise.all([
      fetchJson<PathsResponse>(apiUrl("/api/paths")),
      fetchJson<RankingEntry[]>(apiUrl("/api/rankings")),
      fetchJson<Agent[]>(apiUrl("/api/agents")),
      fetchJson<MarketStatusResponse>(apiUrl("/api/market/status")),
    ]);
    setPaths(nextPaths);
    setRankings(nextRankings);
    setAgents(nextAgents);
    setMarketStatus(nextMarketStatus);
  }

  useEffect(() => {
    void load().catch((loadError: Error) => {
      setError(loadError.message);
    });
  }, []);

  useEffect(() => {
    const codes = marketStatus?.codes ?? [];
    if (!codes.length) {
      setSelectedCode("");
      setMarketBars(null);
      return;
    }
    if (!selectedCode || !codes.some((item) => item.code === selectedCode)) {
      setSelectedCode(codes[0].code);
    }
  }, [marketStatus, selectedCode]);

  useEffect(() => {
    if (!selectedCode) {
      return;
    }
    void fetchJson<MarketBarsResponse>(apiUrl(`/api/market/bars?code=${encodeURIComponent(selectedCode)}`))
      .then((payload) => {
        setMarketBars(payload);
      })
      .catch((loadError: Error) => {
        setError(loadError.message);
      });
  }, [selectedCode]);

  async function handleRefreshMarket(): Promise<void> {
    setError("");
    try {
      await fetchJson<{ status: string }>(apiUrl("/api/market/refresh"), { method: "POST" });
      await load();
    } catch (refreshError) {
      setError((refreshError as Error).message);
    }
  }

  async function handleParseToday(): Promise<void> {
    setIsParsingToday(true);
    setError("");
    try {
      const result = await fetchJson<MarketParseResponse>(apiUrl("/api/market/parse-today"), { method: "POST" });
      setLastParse(result);
      await load();
    } catch (parseError) {
      setError((parseError as Error).message);
    } finally {
      setIsParsingToday(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setIsSaving(true);
    setError("");
    try {
      await fetchJson<Agent>(apiUrl("/api/agents"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_id: draft.agent_id,
          display_name: draft.display_name,
          token_secret: draft.token_secret,
          initial_cash: Number(draft.initial_cash),
        }),
      });
      setDraft(EMPTY_DRAFT);
      await load();
    } catch (submitError) {
      setError((submitError as Error).message);
    } finally {
      setIsSaving(false);
    }
  }

  const codes = marketStatus?.codes ?? [];
  const selectedStatus = codes.find((item) => item.code === selectedCode) ?? null;

  return (
    <div className="app-shell">
      <header className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Quant Arena</p>
          <h1>Agent trading monitor</h1>
          <p className="subhead">
            Market refresh now exposes public market-data status directly in the UI, including daily-bar coverage and
            live 5-minute bars for tracked codes.
          </p>
        </div>
        <div className="hero-actions">
          <div className="button-row">
            <button type="button" onClick={() => void handleRefreshMarket()}>
              Refresh market
            </button>
            <button type="button" onClick={() => void handleParseToday()} disabled={isParsingToday}>
              {isParsingToday ? "Parsing..." : "Parse today if missing"}
            </button>
          </div>
          <p className="action-hint">Refresh updates the public market-data root first, then runs order matching.</p>
        </div>
      </header>

      {error ? <div className="banner error">{error}</div> : null}

      <main className="grid">
        <section className="panel span-2">
          <div className="panel-head">
            <h2>Market sync</h2>
          </div>
          {paths ? (
            <div className="stack">
              <div className="metric-strip">
                <article className="metric-card">
                  <span className="metric-label">Market data root</span>
                  <code>{paths.market_data_root}</code>
                </article>
                <article className="metric-card">
                  <span className="metric-label">Tracked codes</span>
                  <strong>{marketStatus?.tracked_codes.length ?? 0}</strong>
                </article>
                <article className="metric-card">
                  <span className="metric-label">Ranking date</span>
                  <strong>{rankingDate ?? "No snapshot yet"}</strong>
                </article>
              </div>
              <div className="path-grid">
                <article className="path-card">
                  <h3>Market data root</h3>
                  <ul className="compact-list">
                    <li><code>{paths.market_data_root}/5min-bars</code></li>
                    <li>Persisted 5-minute bars by code and trade date.</li>
                    <li><code>{paths.market_data_root}/daily-bars</code></li>
                    <li>Persisted daily OHLCV bars by code and trade date.</li>
                  </ul>
                </article>
              </div>
              {lastParse ? (
                <article className="path-card">
                  <h3>Last manual parse</h3>
                  <ul className="compact-list">
                    <li>Trade date: <strong>{lastParse.trade_date}</strong></li>
                    <li>Tracked today: <strong>{lastParse.tracked_codes.join(", ") || "None"}</strong></li>
                    <li>Parsed daily: <strong>{lastParse.parsed_daily_codes.join(", ") || "None"}</strong></li>
                    <li>Parsed 5m: <strong>{lastParse.parsed_five_minute_codes.join(", ") || "None"}</strong></li>
                  </ul>
                </article>
              ) : null}
            </div>
          ) : (
            <p className="empty">Loading runtime paths...</p>
          )}
        </section>

        <section className="panel span-2">
          <div className="panel-head">
            <h2>Market data status</h2>
          </div>
          {codes.length ? (
            <table>
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Tracked</th>
                  <th>Daily bar</th>
                  <th>5m bars</th>
                  <th>Last 5m time</th>
                </tr>
              </thead>
              <tbody>
                {codes.map((item) => (
                  <tr
                    key={item.code}
                    className={item.code === selectedCode ? "selected-row" : undefined}
                    onClick={() => setSelectedCode(item.code)}
                  >
                    <td><code>{item.code}</code></td>
                    <td>{marketStatus?.tracked_codes.includes(item.code) ? "Yes" : "No"}</td>
                    <td>{item.latest_daily_bar_date ?? "Missing"}</td>
                    <td>{item.five_minute_bar_count}</td>
                    <td>{formatDateTime(item.last_five_minute_bar_time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty">No tracked or cached codes yet. Submit an order or hold a position first.</p>
          )}
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Selected code</h2>
          </div>
          {selectedStatus ? (
            <div className="stack">
              <article className="path-card">
                <h3><code>{selectedStatus.code}</code></h3>
                <ul className="compact-list">
                  <li>Latest daily bar: <strong>{selectedStatus.latest_daily_bar_date ?? "None"}</strong></li>
                  <li>Latest 5m trade date: <strong>{selectedStatus.latest_five_minute_bar_date ?? "None"}</strong></li>
                </ul>
              </article>
              {marketBars?.daily_bar ? (
                <article className="path-card">
                  <h3>Daily bar {marketBars.trade_date}</h3>
                  <div className="ohlc-grid">
                    <span>Open {formatNumber(marketBars.daily_bar.open_price)}</span>
                    <span>High {formatNumber(marketBars.daily_bar.high_price)}</span>
                    <span>Low {formatNumber(marketBars.daily_bar.low_price)}</span>
                    <span>Close {formatNumber(marketBars.daily_bar.close_price)}</span>
                  </div>
                </article>
              ) : (
                <p className="empty">No daily bar for the selected code/date.</p>
              )}
            </div>
          ) : (
            <p className="empty">Select a code to inspect public market data.</p>
          )}
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>5-minute bars</h2>
          </div>
          {marketBars?.five_minute_bars.length ? (
            <div className="bars-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Open</th>
                    <th>High</th>
                    <th>Low</th>
                    <th>Close</th>
                    <th>Volume</th>
                  </tr>
                </thead>
                <tbody>
                  {marketBars.five_minute_bars.map((bar) => (
                    <tr key={bar.bar_time}>
                      <td>{formatDateTime(bar.bar_time)}</td>
                      <td>{formatNumber(bar.open_price)}</td>
                      <td>{formatNumber(bar.high_price)}</td>
                      <td>{formatNumber(bar.low_price)}</td>
                      <td>{formatNumber(bar.close_price)}</td>
                      <td>{formatNumber(bar.volume)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty">No 5-minute bars available for the selected code/date.</p>
          )}
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Rankings</h2>
          </div>
          {rankings.length ? (
            <table>
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Equity</th>
                  <th>Return</th>
                  <th>Realized</th>
                  <th>Unrealized</th>
                </tr>
              </thead>
              <tbody>
                {rankings.map((entry) => (
                  <tr key={entry.agent_id}>
                    <td>{entry.display_name}</td>
                    <td>{formatCurrency(entry.total_equity)}</td>
                    <td>{entry.return_pct.toFixed(2)}%</td>
                    <td>{formatCurrency(entry.realized_pnl)}</td>
                    <td>{formatCurrency(entry.unrealized_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty">No rankings yet.</p>
          )}
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Agents</h2>
          </div>
          <ul className="list">
            {agents.length ? (
              agents.map((agent) => (
                <li key={agent.agent_id}>
                  <strong>{agent.display_name}</strong>
                  <br />
                  <code>{agent.agent_id}</code>
                  <br />
                  Initial cash: {formatCurrency(agent.initial_cash)}
                  <br />
                  Token header: <code>{agent.token_header_name}</code>
                </li>
              ))
            ) : (
              <li>No agents registered.</li>
            )}
          </ul>
        </section>

        <section className="panel span-2">
          <div className="panel-head">
            <h2>Create agent</h2>
          </div>
          <p className="empty">
            Adding an agent creates private files under its own agent directory. Public market bars remain in the separate market-data root.
          </p>
          <form className="form" onSubmit={(event) => void handleSubmit(event)}>
            <input
              value={draft.agent_id}
              onChange={(event) => setDraft((current) => ({ ...current, agent_id: event.target.value }))}
              placeholder="agent id"
              required
            />
            <input
              value={draft.display_name}
              onChange={(event) => setDraft((current) => ({ ...current, display_name: event.target.value }))}
              placeholder="display name"
              required
            />
            <input
              value={draft.token_secret}
              onChange={(event) => setDraft((current) => ({ ...current, token_secret: event.target.value }))}
              placeholder="token secret"
              required
            />
            <input
              value={draft.initial_cash}
              onChange={(event) => setDraft((current) => ({ ...current, initial_cash: event.target.value }))}
              placeholder="initial cash"
              type="number"
              min="1"
              step="0.01"
              required
            />
            <button type="submit" disabled={isSaving}>
              {isSaving ? "Creating..." : "Create"}
            </button>
          </form>
        </section>
      </main>
    </div>
  );
}
