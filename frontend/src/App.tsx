import { FormEvent, useEffect, useState } from "react";

type PathsResponse = {
  config_path: string;
  agents_root: string;
  market_data_root: string;
};

type Agent = {
  agent_id: string;
  display_name: string;
  token_secret: string;
  initial_cash: number;
  sell_constraint: "t_plus_one";
  enabled: boolean;
};

type RankingEntry = {
  trade_date: string;
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

type CodeNameEntry = {
  code: string;
  name: string;
};

type CodeSearchResponse = {
  query: string;
  page: number;
  page_size: number;
  total: number;
  items: CodeNameEntry[];
  last_refreshed_at: string | null;
  auto_refresh_enabled: boolean;
};

type CodeRefreshResponse = {
  refreshed_at: string;
  entry_count: number;
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

type MarketParseJob = {
  job_id: string;
  status: "pending" | "running" | "completed" | "failed";
  start_date: string;
  end_date: string;
  tracked_codes_total: number;
  tracked_codes_completed: number;
  current_code: string | null;
  current_step: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  daily_rows_written: number;
  five_minute_rows_written: number;
  skipped_daily_codes: number;
  skipped_five_minute_codes: number;
  message: string | null;
  error: string | null;
};

type MarketRangeParseDraft = {
  start_date: string;
  end_date: string;
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

const EMPTY_RANGE_PARSE_DRAFT: MarketRangeParseDraft = {
  start_date: "",
  end_date: "",
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
  const [codeSearch, setCodeSearch] = useState<CodeSearchResponse | null>(null);
  const [codeQueryInput, setCodeQueryInput] = useState("");
  const [submittedCodeQuery, setSubmittedCodeQuery] = useState("");
  const [codePage, setCodePage] = useState(1);
  const [codePageSize, setCodePageSize] = useState(20);
  const [selectedCode, setSelectedCode] = useState<string>("");
  const [marketBars, setMarketBars] = useState<MarketBarsResponse | null>(null);
  const [lastParse, setLastParse] = useState<MarketParseResponse | null>(null);
  const [parseJobs, setParseJobs] = useState<MarketParseJob[]>([]);
  const [lastCodeRefresh, setLastCodeRefresh] = useState<CodeRefreshResponse | null>(null);
  const [draft, setDraft] = useState<AgentDraft>(EMPTY_DRAFT);
  const [rangeParseDraft, setRangeParseDraft] = useState<MarketRangeParseDraft>(EMPTY_RANGE_PARSE_DRAFT);
  const [error, setError] = useState<string>("");
  const [isSaving, setIsSaving] = useState(false);
  const [isParsingToday, setIsParsingToday] = useState(false);
  const [isStartingRangeParse, setIsStartingRangeParse] = useState(false);
  const [isRefreshingCodes, setIsRefreshingCodes] = useState(false);
  const rankingDate = rankings[0]?.trade_date ?? null;

  async function loadCore(): Promise<void> {
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

  async function loadCodeSearch(query: string, page: number, pageSize: number): Promise<void> {
    const params = new URLSearchParams({
      query,
      page: String(page),
      page_size: String(pageSize),
    });
    const payload = await fetchJson<CodeSearchResponse>(apiUrl(`/api/market/codes?${params.toString()}`));
    setCodeSearch(payload);
  }

  async function loadParseJobs(): Promise<void> {
    const payload = await fetchJson<MarketParseJob[]>(apiUrl("/api/market/parse-jobs"));
    setParseJobs(payload);
  }

  useEffect(() => {
    void loadCore().catch((loadError: Error) => {
      setError(loadError.message);
    });
  }, []);

  useEffect(() => {
    void loadParseJobs().catch((loadError: Error) => {
      setError(loadError.message);
    });
    const interval = window.setInterval(() => {
      void loadParseJobs().catch((loadError: Error) => {
        setError(loadError.message);
      });
    }, 2000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    void loadCodeSearch(submittedCodeQuery, codePage, codePageSize).catch((loadError: Error) => {
      setError(loadError.message);
    });
  }, [submittedCodeQuery, codePage, codePageSize]);

  useEffect(() => {
    const items = codeSearch?.items ?? [];
    if (!items.length) {
      setSelectedCode("");
      setMarketBars(null);
      return;
    }
    if (!selectedCode || !items.some((item) => item.code === selectedCode)) {
      setSelectedCode(items[0].code);
    }
  }, [codeSearch, selectedCode]);

  useEffect(() => {
    if (!selectedCode) {
      return;
    }
    void fetchJson<MarketBarsResponse>(apiUrl(`/api/market/bars?code=${encodeURIComponent(selectedCode)}`))
      .then((payload) => {
        setMarketBars(payload);
      })
      .catch((loadError: Error) => {
        setMarketBars(null);
        setError(loadError.message);
      });
  }, [selectedCode]);

  async function handleRefreshMarket(): Promise<void> {
    setError("");
    try {
      await fetchJson<{ status: string }>(apiUrl("/api/market/refresh"), { method: "POST" });
      await loadCore();
    } catch (refreshError) {
      setError((refreshError as Error).message);
    }
  }

  async function handleRefreshCodes(): Promise<void> {
    setIsRefreshingCodes(true);
    setError("");
    try {
      const result = await fetchJson<CodeRefreshResponse>(apiUrl("/api/market/codes/refresh"), { method: "POST" });
      setLastCodeRefresh(result);
      await loadCodeSearch(submittedCodeQuery, codePage, codePageSize);
    } catch (refreshError) {
      setError((refreshError as Error).message);
    } finally {
      setIsRefreshingCodes(false);
    }
  }

  async function handleParseToday(): Promise<void> {
    setIsParsingToday(true);
    setError("");
    try {
      const result = await fetchJson<MarketParseResponse>(apiUrl("/api/market/parse-today"), { method: "POST" });
      setLastParse(result);
      await loadCore();
    } catch (parseError) {
      setError((parseError as Error).message);
    } finally {
      setIsParsingToday(false);
    }
  }

  async function handleStartRangeParse(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setIsStartingRangeParse(true);
    setError("");
    try {
      await fetchJson<MarketParseJob>(apiUrl("/api/market/parse-jobs"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(rangeParseDraft),
      });
      await loadParseJobs();
    } catch (parseError) {
      setError((parseError as Error).message);
    } finally {
      setIsStartingRangeParse(false);
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
      await loadCore();
    } catch (submitError) {
      setError((submitError as Error).message);
    } finally {
      setIsSaving(false);
    }
  }

  function handleSearchSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    setCodePage(1);
    setSubmittedCodeQuery(codeQueryInput.trim());
  }

  const codes = marketStatus?.codes ?? [];
  const selectedStatus = codes.find((item) => item.code === selectedCode) ?? null;
  const selectedCodeEntry = codeSearch?.items.find((item) => item.code === selectedCode) ?? null;
  const pageCount = codeSearch ? Math.max(1, Math.ceil(codeSearch.total / codeSearch.page_size)) : 1;
  const latestParseJob = parseJobs[0] ?? null;

  return (
    <div className="app-shell">
      <header className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Quant Arena</p>
          <h1>Agent trading monitor</h1>
          <p className="subhead">
            Shared market data stays under one root, while code-directory search is paged so the frontend never pulls the
            entire universe at once.
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
            <button type="button" onClick={() => void handleRefreshCodes()} disabled={isRefreshingCodes}>
              {isRefreshingCodes ? "Refreshing..." : "Refresh codes.csv"}
            </button>
          </div>
          <p className="action-hint">Code-name refresh stays separate from bar parsing and is always loaded page by page.</p>
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
              <div className="metric-strip metric-strip-wide">
                <article className="metric-card">
                  <span className="metric-label">Market data root</span>
                  <code>{paths.market_data_root}</code>
                </article>
                <article className="metric-card">
                  <span className="metric-label">Tracked codes</span>
                  <strong>{marketStatus?.tracked_codes.length ?? 0}</strong>
                </article>
                <article className="metric-card">
                  <span className="metric-label">Code rows</span>
                  <strong>{codeSearch?.total ?? 0}</strong>
                </article>
                <article className="metric-card">
                  <span className="metric-label">Ranking date</span>
                  <strong>{rankingDate ?? "No snapshot yet"}</strong>
                </article>
              </div>
              <div className="path-grid">
                <article className="path-card">
                  <h3>Market data files</h3>
                  <ul className="compact-list">
                    <li><code>{paths.market_data_root}/codes.csv</code></li>
                    <li>Shared code-name reference file from baostock.</li>
                    <li><code>{paths.market_data_root}/bars/&lt;date&gt;/daily.csv</code></li>
                    <li>One daily row per code for that trade date.</li>
                    <li><code>{paths.market_data_root}/bars/&lt;date&gt;/5min/&lt;minute&gt;.csv</code></li>
                    <li>5-minute rows partitioned by date and minute under the same day root.</li>
                  </ul>
                </article>
                <article className="path-card">
                  <h3>Code index status</h3>
                  <ul className="compact-list">
                    <li>Auto refresh: <strong>{codeSearch?.auto_refresh_enabled ? "Enabled" : "Disabled"}</strong></li>
                    <li>Last refreshed: <strong>{formatDateTime(codeSearch?.last_refreshed_at ?? null)}</strong></li>
                    {lastCodeRefresh ? (
                      <li>Last manual refresh: <strong>{lastCodeRefresh.entry_count} rows at {formatDateTime(lastCodeRefresh.refreshed_at)}</strong></li>
                    ) : null}
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
              <article className="path-card">
                <h3>Range parse jobs</h3>
                <ul className="compact-list">
                  <li>Jobs in memory: <strong>{parseJobs.length}</strong></li>
                  <li>Latest status: <strong>{latestParseJob?.status ?? "None"}</strong></li>
                  <li>Latest progress: <strong>{latestParseJob ? `${latestParseJob.tracked_codes_completed} / ${latestParseJob.tracked_codes_total}` : "None"}</strong></li>
                </ul>
              </article>
            </div>
          ) : (
            <p className="empty">Loading runtime paths...</p>
          )}
        </section>

        <section className="panel span-2">
          <div className="panel-head">
            <h2>Range parse</h2>
          </div>
          <form className="range-parse-form" onSubmit={(event) => void handleStartRangeParse(event)}>
            <input
              type="date"
              value={rangeParseDraft.start_date}
              onChange={(event) => setRangeParseDraft((current) => ({ ...current, start_date: event.target.value }))}
              required
            />
            <input
              type="date"
              value={rangeParseDraft.end_date}
              onChange={(event) => setRangeParseDraft((current) => ({ ...current, end_date: event.target.value }))}
              required
            />
            <button type="submit" disabled={isStartingRangeParse}>
              {isStartingRangeParse ? "Starting..." : "Start range parse"}
            </button>
          </form>
          {parseJobs.length ? (
            <div className="bars-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Date range</th>
                    <th>Progress</th>
                    <th>Current code</th>
                    <th>Rows written</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {parseJobs.map((job) => (
                    <tr key={job.job_id}>
                      <td>{job.status}</td>
                      <td>{job.start_date} to {job.end_date}</td>
                      <td>{job.tracked_codes_completed} / {job.tracked_codes_total}</td>
                      <td>{job.current_code ?? "None"}{job.current_step ? ` (${job.current_step})` : ""}</td>
                      <td>
                        daily {job.daily_rows_written}, 5m {job.five_minute_rows_written}
                        <br />
                        skipped daily {job.skipped_daily_codes}, skipped 5m {job.skipped_five_minute_codes}
                      </td>
                      <td>
                        {job.error ? <span className="error-text">{job.error}</span> : "None"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty">No parse jobs yet.</p>
          )}
        </section>

        <section className="panel span-2">
          <div className="panel-head">
            <h2>Code directory</h2>
          </div>
          <form className="search-row" onSubmit={handleSearchSubmit}>
            <input
              value={codeQueryInput}
              onChange={(event) => setCodeQueryInput(event.target.value)}
              placeholder="Search code or name"
            />
            <select
              value={codePageSize}
              onChange={(event) => {
                setCodePage(1);
                setCodePageSize(Number(event.target.value));
              }}
            >
              <option value={20}>20 / page</option>
              <option value={50}>50 / page</option>
              <option value={100}>100 / page</option>
            </select>
            <button type="submit">Search</button>
          </form>
          {codeSearch?.items.length ? (
            <>
              <table>
                <thead>
                  <tr>
                    <th>Code</th>
                    <th>Name</th>
                  </tr>
                </thead>
                <tbody>
                  {codeSearch.items.map((item) => (
                    <tr
                      key={item.code}
                      className={item.code === selectedCode ? "selected-row" : undefined}
                      onClick={() => setSelectedCode(item.code)}
                    >
                      <td><code>{item.code}</code></td>
                      <td>{item.name}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="pager-row">
                <span>
                  Page <strong>{codeSearch.page}</strong> / <strong>{pageCount}</strong>, total <strong>{codeSearch.total}</strong>
                </span>
                <div className="button-row">
                  <button type="button" onClick={() => setCodePage((current) => Math.max(1, current - 1))} disabled={codeSearch.page <= 1}>
                    Prev
                  </button>
                  <button
                    type="button"
                    onClick={() => setCodePage((current) => current + 1)}
                    disabled={codeSearch.page >= pageCount}
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          ) : (
            <p className="empty">No code rows loaded yet. Refresh codes.csv or adjust the search.</p>
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
            <p className="empty">No tracked codes with stored bars yet.</p>
          )}
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Selected code</h2>
          </div>
          {selectedCode ? (
            <div className="stack">
              <article className="path-card">
                <h3><code>{selectedCode}</code></h3>
                <ul className="compact-list">
                  <li>Name: <strong>{selectedCodeEntry?.name ?? "Unknown"}</strong></li>
                  <li>Latest daily bar: <strong>{selectedStatus?.latest_daily_bar_date ?? "None"}</strong></li>
                  <li>Latest 5m trade date: <strong>{selectedStatus?.latest_five_minute_bar_date ?? "None"}</strong></li>
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
            <p className="empty">Search or select a code to inspect market data.</p>
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
                  <th>Date</th>
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
                    <td>{entry.trade_date}</td>
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
                  Status: {agent.enabled ? "Enabled" : "Disabled"}
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
            Adding an agent creates private files under its own agent directory. Shared code names and market bars stay under the separate market-data root.
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
