import { startTransition, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type AgentResponse = {
  agent_id: string;
  display_name: string;
  initial_cash: number;
  enabled: boolean;
  role: "normal" | "monitor";
};

type AgentCreatedResponse = {
  agent: AgentResponse;
  token_secret: string;
};

type PositionView = {
  code: string;
  quantity: number;
  sellable_quantity: number;
  avg_cost: number;
  market_price: number | null;
  market_value: number;
  unrealized_pnl: number;
};

type OrderRecord = {
  order_id: string;
  code: string;
  side: "buy" | "sell";
  quantity: number;
  limit_price: number;
  comment: string;
  status: string;
  submitted_at: string;
  filled_at: string | null;
  canceled_at: string | null;
  rejection_reason: string | null;
};

type FillRecord = {
  fill_id: string;
  order_id: string;
  code: string;
  side: "buy" | "sell";
  quantity: number;
  executed_at: string;
  executed_price: number;
  commission: number;
  stamp_tax: number;
};

type PortfolioResponse = {
  agent_id: string;
  cash: number;
  market_value: number;
  total_equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  positions: PositionView[];
  pending_orders: OrderRecord[];
  as_of: string | null;
};

type OperationListResponse = {
  orders: OrderRecord[];
  fills: FillRecord[];
};

type EquityPoint = {
  trade_date: string;
  cash: number;
  market_value: number;
  total_equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
};

type AgentSnapshotResponse = {
  agent: AgentResponse;
  portfolio: PortfolioResponse;
  operations: OperationListResponse;
  equity: EquityPoint[];
};

type DailyReportSummary = {
  trade_date: string;
  updated_at: string;
};

type DailyReport = {
  trade_date: string;
  content: string;
  updated_at: string;
};

type DailyReportPage = {
  items: DailyReportSummary[];
  total: number;
  page: number;
  page_size: number;
};

type RankingEntry = {
  trade_date: string;
  agent_id: string;
  display_name: string;
  cash: number;
  market_value: number;
  total_equity: number;
  return_pct: number;
  realized_pnl: number;
  unrealized_pnl: number;
};

type CreateAgentForm = {
  agent_id: string;
  display_name: string;
  initial_cash: string;
  role: "normal" | "monitor";
};

const BASE_URL = import.meta.env.BASE_URL.replace(/\/+$/, "");
const API_BASE = (import.meta.env.VITE_API_BASE ?? BASE_URL).replace(/\/+$/, "");

const defaultCreateAgentForm: CreateAgentForm = {
  agent_id: "",
  display_name: "",
  initial_cash: "100000",
  role: "normal",
};

const ORDERS_PAGE_SIZE = 8;
const REPORTS_PAGE_SIZE = 10;

function getAgentIdFromUrl(): string {
  return new URLSearchParams(window.location.search).get("agent-id") ?? "";
}

function setAgentIdInUrl(agentId: string): void {
  const url = new URL(window.location.href);
  if (agentId) {
    url.searchParams.set("agent-id", agentId);
  } else {
    url.searchParams.delete("agent-id");
  }
  window.history.replaceState(null, "", url);
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
    throw new Error(body.detail ?? `HTTP ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function formatMoney(value: number | null | undefined): string {
  if (value == null) {
    return "--";
  }
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 2,
  }).format(value);
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value == null) {
    return "--";
  }
  return value.toFixed(digits);
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDateShort(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "2-digit" })
    .format(new Date(value))
    .toUpperCase();
}

function percentClass(value: number): string {
  if (value > 0) {
    return "up";
  }
  if (value < 0) {
    return "down";
  }
  return "flat";
}

function signedPct(value: number, digits = 2): string {
  const sign = value > 0 ? "+" : value < 0 ? "" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

type ChartGeometry = {
  W: number;
  H: number;
  P: number;
  pathLine: string;
  pathFill: string;
  gridY: { y: number; value: number }[];
  endX: number;
  endY: number;
  min: number;
  max: number;
  first: string;
  last: string;
};

function buildEquityChart(points: EquityPoint[]): ChartGeometry | null {
  if (points.length < 2) {
    return null;
  }
  const W = 1000;
  const H = 220;
  const P = 14;
  const values = points.map((p) => p.total_equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const xAt = (i: number) => P + (i / (points.length - 1)) * (W - 2 * P);
  const yAt = (v: number) => P + (1 - (v - min) / range) * (H - 2 * P);

  const lineCommands = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(2)} ${yAt(p.total_equity).toFixed(2)}`)
    .join(" ");
  const fillCommands = `${lineCommands} L${xAt(points.length - 1).toFixed(2)} ${(H - P).toFixed(2)} L${xAt(0).toFixed(2)} ${(H - P).toFixed(2)} Z`;

  const gridY = [0, 0.25, 0.5, 0.75, 1].map((t) => ({
    y: P + t * (H - 2 * P),
    value: max - t * range,
  }));

  return {
    W,
    H,
    P,
    pathLine: lineCommands,
    pathFill: fillCommands,
    gridY,
    endX: xAt(points.length - 1),
    endY: yAt(points[points.length - 1].total_equity),
    min,
    max,
    first: points[0].trade_date,
    last: points[points.length - 1].trade_date,
  };
}

function todayStamp() {
  const now = new Date();
  const iso = now.toISOString().slice(0, 10);
  const label = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", year: "numeric" })
    .format(now)
    .toUpperCase();
  const weekday = new Intl.DateTimeFormat("en-US", { weekday: "long" }).format(now).toUpperCase();
  const start = new Date("2025-01-01T00:00:00Z").getTime();
  const days = Math.floor((now.getTime() - start) / 86400000) + 1;
  const edition = String(Math.max(days, 1)).padStart(4, "0");
  return { iso, label, edition, weekday };
}

export function AShareApp() {
  const [agents, setAgents] = useState<AgentResponse[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string>("");
  const [snapshot, setSnapshot] = useState<AgentSnapshotResponse | null>(null);
  const [rankings, setRankings] = useState<RankingEntry[]>([]);
  const [createAgentForm, setCreateAgentForm] = useState<CreateAgentForm>(defaultCreateAgentForm);
  const [loadingAgents, setLoadingAgents] = useState(true);
  const [loadingSnapshot, setLoadingSnapshot] = useState(false);
  const [loadingRankings, setLoadingRankings] = useState(true);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [createdToken, setCreatedToken] = useState<string>("");
  const [createdAgentId, setCreatedAgentId] = useState<string>("");
  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const [ordersPage, setOrdersPage] = useState(1);
  const [reportsPage, setReportsPage] = useState(1);
  const [reportsList, setReportsList] = useState<DailyReportPage | null>(null);
  const [loadingReportsList, setLoadingReportsList] = useState(false);
  const [selectedReportDate, setSelectedReportDate] = useState<string>("");
  const [selectedReport, setSelectedReport] = useState<DailyReport | null>(null);
  const [loadingReportDetail, setLoadingReportDetail] = useState(false);

  useEffect(() => {
    void refreshAgents(getAgentIdFromUrl());
    void refreshRankings();
  }, []);

  useEffect(() => {
    const handlePopState = () => {
      setSelectedAgentId(getAgentIdFromUrl());
    };
    window.addEventListener("popstate", handlePopState);
    return () => {
      window.removeEventListener("popstate", handlePopState);
    };
  }, []);

  useEffect(() => {
    setAgentIdInUrl(selectedAgentId);
    if (!selectedAgentId) {
      setSnapshot(null);
      setReportsList(null);
      setSelectedReport(null);
      setSelectedReportDate("");
      return;
    }
    setOrdersPage(1);
    setReportsPage(1);
    setSelectedReport(null);
    setSelectedReportDate("");
    void refreshSnapshot(selectedAgentId);
  }, [selectedAgentId]);

  useEffect(() => {
    if (!selectedAgentId) {
      return;
    }
    void refreshReports(selectedAgentId, reportsPage);
  }, [selectedAgentId, reportsPage]);

  async function refreshAgents(preferredAgentId?: string) {
    setLoadingAgents(true);
    setError("");
    try {
      const data = await apiFetch<AgentResponse[]>("/api/agents");
      setAgents(data);
      startTransition(() => {
        const nextAgentId =
          preferredAgentId && data.some((agent) => agent.agent_id === preferredAgentId)
            ? preferredAgentId
            : "";
        setSelectedAgentId(nextAgentId);
      });
    } catch (fetchError) {
      setError((fetchError as Error).message);
    } finally {
      setLoadingAgents(false);
    }
  }

  async function refreshSnapshot(agentId: string) {
    setLoadingSnapshot(true);
    setError("");
    try {
      const data = await apiFetch<AgentSnapshotResponse>(`/api/agents/${agentId}`);
      setSnapshot(data);
    } catch (fetchError) {
      setError((fetchError as Error).message);
      setSnapshot(null);
    } finally {
      setLoadingSnapshot(false);
    }
  }

  async function refreshReports(agentId: string, page: number) {
    setLoadingReportsList(true);
    try {
      const data = await apiFetch<DailyReportPage>(
        `/api/agents/${agentId}/daily-reports?page=${page}&page_size=${REPORTS_PAGE_SIZE}`,
      );
      setReportsList(data);
    } catch (fetchError) {
      setError((fetchError as Error).message);
      setReportsList(null);
    } finally {
      setLoadingReportsList(false);
    }
  }

  async function loadReportDetail(agentId: string, tradeDate: string) {
    setSelectedReportDate(tradeDate);
    setLoadingReportDetail(true);
    try {
      const data = await apiFetch<DailyReport>(
        `/api/agents/${agentId}/daily-reports/${tradeDate}`,
      );
      setSelectedReport(data);
    } catch (fetchError) {
      setError((fetchError as Error).message);
      setSelectedReport(null);
    } finally {
      setLoadingReportDetail(false);
    }
  }

  async function refreshRankings() {
    setLoadingRankings(true);
    try {
      const data = await apiFetch<RankingEntry[]>("/api/rankings");
      setRankings(data);
    } catch (fetchError) {
      setError((fetchError as Error).message);
    } finally {
      setLoadingRankings(false);
    }
  }

  async function handleCreateAgent(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setError("");
    setCreatedToken("");
    setCreatedAgentId("");
    try {
      const created = await apiFetch<AgentCreatedResponse>("/api/agents", {
        method: "POST",
        body: JSON.stringify({
          ...createAgentForm,
          initial_cash: Number(createAgentForm.initial_cash),
        }),
      });
      setCreatedToken(created.token_secret);
      setCreatedAgentId(created.agent.agent_id);
      setCreateAgentForm(defaultCreateAgentForm);
      setMessage(`Agent ${created.agent.agent_id} created.`);
      await refreshAgents(created.agent.agent_id);
      await refreshRankings();
    } catch (fetchError) {
      setError((fetchError as Error).message);
    }
  }

  async function handleDeleteAgent(agentId: string) {
    const confirmed = window.confirm(`Delete agent ${agentId}?`);
    if (!confirmed) {
      return;
    }
    setMessage("");
    setError("");
    setCreatedToken("");
    setCreatedAgentId("");
    try {
      await apiFetch<void>(`/api/agents/${agentId}`, { method: "DELETE" });
      setMessage(`Agent ${agentId} deleted.`);
      await refreshAgents();
      await refreshRankings();
    } catch (fetchError) {
      setError((fetchError as Error).message);
    }
  }

  const selectedRanking = snapshot
    ? rankings.find((entry) => entry.agent_id === snapshot.agent.agent_id)
    : null;
  const latestEquity = snapshot && snapshot.equity.length > 0 ? snapshot.equity[snapshot.equity.length - 1] : null;
  const chart = snapshot ? buildEquityChart(snapshot.equity) : null;
  const agentById = new Map(agents.map((agent) => [agent.agent_id, agent]));
  const orderedOrders = snapshot ? [...snapshot.operations.orders].reverse() : [];
  const fillByOrderId = new Map((snapshot?.operations.fills ?? []).map((fill) => [fill.order_id, fill]));
  const totalOrdersPages = Math.max(1, Math.ceil(orderedOrders.length / ORDERS_PAGE_SIZE));
  const currentOrdersPage = Math.min(ordersPage, totalOrdersPages);
  const visibleOrders = orderedOrders.slice(
    (currentOrdersPage - 1) * ORDERS_PAGE_SIZE,
    currentOrdersPage * ORDERS_PAGE_SIZE,
  );
  const stamp = todayStamp();
  const reportsTotal = reportsList?.total ?? 0;
  const reportsTotalPages = Math.max(1, Math.ceil(reportsTotal / REPORTS_PAGE_SIZE));
  const reportsCurrentPage = Math.min(reportsPage, reportsTotalPages);
  const reportsItems = reportsList?.items ?? [];

  return (
    <div className="wrap reveal">
      <div className="masthead-rail">
        <a className="home-link" href={`${BASE_URL}/`}>
          ← All Markets
        </a>
        <span>
          {stamp.weekday} · {stamp.label} · No. {stamp.edition}
        </span>
      </div>
      <div className="rule-double" />
      <header className="masthead">
        <div>
          <h1 className="masthead-title">
            A · <em>Share</em>
            <span className="glyph">沪</span>
          </h1>
          <div className="masthead-han">沪 深 京 通 鉴</div>
        </div>
        <div className="masthead-meta">
          <span>BUREAU OF SIMULATED EQUITIES</span>
          <span>SHANGHAI · SHENZHEN · BEIJING</span>
          <span>SETTLEMENT T+1 · STAMP 0.05% · COMM 0.025%</span>
          <span>
            {loadingAgents || loadingRankings ? (
              <>
                <span className="dot dot-soft" />
                UPDATING
              </>
            ) : (
              <>
                <span className="dot dot-rise" />
                {rankings.length} AGENTS RANKED
              </>
            )}
          </span>
        </div>
      </header>
      <div className="rule-thick" />

      {(message || error) && (
        <div className={`notice ${error ? "error" : "ok"}`}>{error || message}</div>
      )}

      <main className="board-grid">
        <aside className="board-rail">
          <div className="section-head">
            <h3>Roster</h3>
            <span className="meta">Ranked · Total Equity</span>
          </div>
          <div className="roster">
            {rankings.map((entry, index) => {
              const agent = agentById.get(entry.agent_id);
              const isActive = selectedAgentId === entry.agent_id;
              return (
                <button
                  key={entry.agent_id}
                  type="button"
                  className={`roster-row ${isActive ? "is-active" : ""}`}
                  onClick={() => setSelectedAgentId(entry.agent_id)}
                >
                  <span className="roster-rank">{String(index + 1).padStart(2, "0")}</span>
                  <span>
                    <div className="roster-name">{entry.display_name}</div>
                    <div className="roster-id">{entry.agent_id}</div>
                    {agent && (
                      <span
                        className={`roster-pill ${agent.enabled ? "live" : ""}`}
                        style={{ marginTop: 8, display: "inline-block" }}
                      >
                        {agent.enabled ? "LIVE" : "OFF"} · {agent.role.toUpperCase()}
                      </span>
                    )}
                  </span>
                  <span className="roster-stats">
                    <span className="roster-equity">{formatMoney(entry.total_equity)}</span>
                    <span className={`roster-return ${percentClass(entry.return_pct)}`}>
                      {signedPct(entry.return_pct)}
                    </span>
                  </span>
                </button>
              );
            })}
            {!loadingRankings && rankings.length === 0 && (
              <p className="empty-line">No rankings yet · enlist an agent below</p>
            )}
          </div>

          <form className="form" onSubmit={handleCreateAgent}>
            <div className="section-head" style={{ borderBottomWidth: 1, marginBottom: 0 }}>
              <h3 style={{ fontSize: 22 }}>Enlist</h3>
              <span className="meta">New Agent</span>
            </div>
            <div className="form-grid">
              <div className="field field-half">
                <label htmlFor="agent_id">Agent ID</label>
                <input
                  id="agent_id"
                  value={createAgentForm.agent_id}
                  onChange={(event) =>
                    setCreateAgentForm((prev) => ({ ...prev, agent_id: event.target.value }))
                  }
                  placeholder="trader-01"
                  required
                />
              </div>
              <div className="field field-half">
                <label htmlFor="initial_cash">Initial Cash</label>
                <input
                  id="initial_cash"
                  value={createAgentForm.initial_cash}
                  onChange={(event) =>
                    setCreateAgentForm((prev) => ({ ...prev, initial_cash: event.target.value }))
                  }
                  type="number"
                  min="1"
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="display_name">Display Name</label>
                <input
                  id="display_name"
                  value={createAgentForm.display_name}
                  onChange={(event) =>
                    setCreateAgentForm((prev) => ({ ...prev, display_name: event.target.value }))
                  }
                  placeholder="The Iron Pen"
                  required
                />
              </div>
              <div
                className="field select-wrap"
                onBlur={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                    setModeMenuOpen(false);
                  }
                }}
              >
                <label>Mode</label>
                <button
                  className="select-trigger"
                  type="button"
                  aria-haspopup="listbox"
                  aria-expanded={modeMenuOpen}
                  onClick={() => setModeMenuOpen((open) => !open)}
                >
                  <span>{createAgentForm.role === "normal" ? "Normal" : "Monitor"}</span>
                </button>
                {modeMenuOpen && (
                  <div className="select-menu" role="listbox" aria-label="Agent mode">
                    <button
                      className={`select-option ${createAgentForm.role === "normal" ? "is-active" : ""}`}
                      type="button"
                      onClick={() => {
                        setCreateAgentForm((prev) => ({ ...prev, role: "normal" }));
                        setModeMenuOpen(false);
                      }}
                    >
                      Normal · trades the book
                    </button>
                    <button
                      className={`select-option ${createAgentForm.role === "monitor" ? "is-active" : ""}`}
                      type="button"
                      onClick={() => {
                        setCreateAgentForm((prev) => ({ ...prev, role: "monitor" }));
                        setModeMenuOpen(false);
                      }}
                    >
                      Monitor · watches only
                    </button>
                  </div>
                )}
              </div>
            </div>
            <button className="button" type="submit">
              Issue Token
            </button>
            {createdToken && (
              <div className="token-card">
                <div className="token-card-label">One-time token · {createdAgentId}</div>
                <div className="token-card-value">{createdToken}</div>
                <button
                  className="button button-ghost"
                  type="button"
                  onClick={() => void navigator.clipboard.writeText(createdToken)}
                >
                  Copy Token
                </button>
              </div>
            )}
          </form>
        </aside>

        <section className="board-main">
          <div className="snapshot-head">
            <div>
              <h2 className="name">{snapshot?.agent.display_name ?? "Select an Agent"}</h2>
              {snapshot ? (
                <div className="id">
                  {snapshot.agent.agent_id} · {snapshot.agent.role.toUpperCase()} ·{" "}
                  {snapshot.agent.enabled ? "LIVE" : "OFFLINE"}
                </div>
              ) : (
                <div className="id">— pick a name from the roster, the books will open —</div>
              )}
            </div>
            {snapshot && (
              <button
                className="delete"
                type="button"
                onClick={() => void handleDeleteAgent(snapshot.agent.agent_id)}
              >
                Strike from Book
              </button>
            )}
          </div>

          {loadingSnapshot ? (
            <div className="snapshot-empty">Loading the ledger…</div>
          ) : snapshot ? (
            <>
              <section className="stat-row">
                <article className="stat-tile">
                  <p className="label">Total Equity</p>
                  <div className="value">{formatMoney(snapshot.portfolio.total_equity)}</div>
                </article>
                <article className="stat-tile">
                  <p className="label">Cash</p>
                  <div className="value">{formatMoney(snapshot.portfolio.cash)}</div>
                </article>
                <article className="stat-tile">
                  <p className="label">Market Value</p>
                  <div className="value">{formatMoney(snapshot.portfolio.market_value)}</div>
                </article>
                <article className="stat-tile">
                  <p className="label">As Of</p>
                  <div className="value">{formatDateTime(snapshot.portfolio.as_of)}</div>
                </article>
              </section>

              <section className="equity">
                <div className="equity-summary">
                  <p className="label">Equity Curve</p>
                  <h3 className="value">
                    {latestEquity ? formatMoney(latestEquity.total_equity) : "--"}
                  </h3>
                  <p className={`return ${percentClass(selectedRanking?.return_pct ?? 0)}`}>
                    Return {signedPct(selectedRanking?.return_pct ?? 0)}
                  </p>
                  {chart && (
                    <p className="span">
                      {formatDateShort(chart.first)} → {formatDateShort(chart.last)}
                      <br />
                      Range {formatMoney(chart.min)} – {formatMoney(chart.max)}
                    </p>
                  )}
                </div>
                <div className="equity-chart">
                  {chart ? (
                    <svg viewBox={`0 0 ${chart.W} ${chart.H}`} preserveAspectRatio="xMidYMid meet">
                      <rect
                        className="frame"
                        x={chart.P}
                        y={chart.P}
                        width={chart.W - 2 * chart.P}
                        height={chart.H - 2 * chart.P}
                      />
                      {chart.gridY.map((g, i) => (
                        <line
                          key={i}
                          className={`gridline ${i === 0 || i === chart.gridY.length - 1 ? "" : "major"}`}
                          x1={chart.P}
                          x2={chart.W - chart.P}
                          y1={g.y}
                          y2={g.y}
                        />
                      ))}
                      <path className="path-fill" d={chart.pathFill} />
                      <path className="path-line" d={chart.pathLine} vectorEffect="non-scaling-stroke" />
                      <circle className="marker" cx={chart.endX} cy={chart.endY} r={4} />
                      <text className="axis-label" x={chart.W - chart.P} y={chart.gridY[0].y - 6} textAnchor="end">
                        ¥{Math.round(chart.gridY[0].value).toLocaleString("en-US")}
                      </text>
                      <text
                        className="axis-label"
                        x={chart.W - chart.P}
                        y={chart.gridY[chart.gridY.length - 1].y + 14}
                        textAnchor="end"
                      >
                        ¥{Math.round(chart.gridY[chart.gridY.length - 1].value).toLocaleString("en-US")}
                      </text>
                      <text className="axis-label" x={chart.P} y={chart.H - chart.P + 16}>
                        {formatDateShort(chart.first)}
                      </text>
                      <text
                        className="axis-label"
                        x={chart.W - chart.P}
                        y={chart.H - chart.P + 16}
                        textAnchor="end"
                      >
                        {formatDateShort(chart.last)}
                      </text>
                    </svg>
                  ) : (
                    <div className="placeholder">Need at least two equity points</div>
                  )}
                </div>
              </section>

              <section className="table-block">
                <div className="table-head">
                  <h4>Holdings</h4>
                  <div className="table-tools">
                    <span>{snapshot.portfolio.positions.length} lines</span>
                  </div>
                </div>
                <table>
                  <thead>
                    <tr>
                      <th>Code</th>
                      <th className="num">Qty</th>
                      <th className="num">Sellable</th>
                      <th className="num">Avg</th>
                      <th className="num">Last</th>
                      <th className="num">Value</th>
                      <th className="num">Unrealized</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshot.portfolio.positions.map((position) => (
                      <tr key={position.code}>
                        <td className="code">{position.code}</td>
                        <td className="num">{position.quantity}</td>
                        <td className="num">{position.sellable_quantity}</td>
                        <td className="num">{formatNumber(position.avg_cost, 3)}</td>
                        <td className="num">{formatNumber(position.market_price, 3)}</td>
                        <td className="num">{formatMoney(position.market_value)}</td>
                        <td className={`num ${percentClass(position.unrealized_pnl)}`}>
                          {formatMoney(position.unrealized_pnl)}
                        </td>
                      </tr>
                    ))}
                    {snapshot.portfolio.positions.length === 0 && (
                      <tr>
                        <td colSpan={7} className="empty">
                          No positions on the book
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </section>

              <section className="table-block">
                <div className="table-head">
                  <h4>Orders &amp; Fills</h4>
                  <div className="table-tools">
                    <span>{snapshot.operations.orders.length} entries</span>
                    {snapshot.operations.orders.length > 0 && (
                      <div className="pager" aria-label="Orders pagination">
                        <button
                          type="button"
                          onClick={() => setOrdersPage((page) => Math.max(1, page - 1))}
                          disabled={currentOrdersPage === 1}
                        >
                          ← Prev
                        </button>
                        <span className="pager-label">
                          {currentOrdersPage} / {totalOrdersPages}
                        </span>
                        <button
                          type="button"
                          onClick={() => setOrdersPage((page) => Math.min(totalOrdersPages, page + 1))}
                          disabled={currentOrdersPage === totalOrdersPages}
                        >
                          Next →
                        </button>
                      </div>
                    )}
                  </div>
                </div>
                <table>
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Code</th>
                      <th>Side</th>
                      <th className="num">Qty</th>
                      <th className="num">Limit</th>
                      <th className="num">Filled</th>
                      <th>Status</th>
                      <th>Comment</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleOrders.map((order) => {
                      const fill = fillByOrderId.get(order.order_id);
                      const isFilled = order.filled_at != null;
                      const isCanceled = order.canceled_at != null;
                      return (
                        <tr key={order.order_id}>
                          <td>{formatDateTime(order.submitted_at)}</td>
                          <td className="code">{order.code}</td>
                          <td>
                            <span className={`side ${order.side}`}>
                              <span className="indicator" />
                              {order.side}
                            </span>
                          </td>
                          <td className="num">{order.quantity}</td>
                          <td className="num">{formatNumber(order.limit_price, 2)}</td>
                          <td className="num">{fill ? formatNumber(fill.executed_price, 2) : "—"}</td>
                          <td>
                            <div className={`status-cell ${isFilled ? "filled" : ""}`}>
                              {isFilled
                                ? `Filled · ${formatDateTime(order.filled_at)}`
                                : isCanceled
                                ? `Canceled · ${formatDateTime(order.canceled_at)}`
                                : order.status.toUpperCase()}
                            </div>
                            {order.rejection_reason && (
                              <div className="order-meta down">{order.rejection_reason}</div>
                            )}
                          </td>
                          <td className="comment-cell">{order.comment}</td>
                        </tr>
                      );
                    })}
                    {snapshot.operations.orders.length === 0 && (
                      <tr>
                        <td colSpan={8} className="empty">
                          No orders on record
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </section>

              <section className="table-block">
                <div className="table-head">
                  <h4>Daily Reports</h4>
                  <div className="table-tools">
                    <span>{reportsTotal} entries</span>
                    {reportsTotal > REPORTS_PAGE_SIZE && (
                      <div className="pager" aria-label="Daily reports pagination">
                        <button
                          type="button"
                          onClick={() => setReportsPage((page) => Math.max(1, page - 1))}
                          disabled={reportsCurrentPage === 1 || loadingReportsList}
                        >
                          ← Prev
                        </button>
                        <span className="pager-label">
                          {reportsCurrentPage} / {reportsTotalPages}
                        </span>
                        <button
                          type="button"
                          onClick={() =>
                            setReportsPage((page) => Math.min(reportsTotalPages, page + 1))
                          }
                          disabled={
                            reportsCurrentPage === reportsTotalPages || loadingReportsList
                          }
                        >
                          Next →
                        </button>
                      </div>
                    )}
                  </div>
                </div>
                <div className="reports">
                  <ul className="reports-list">
                    {loadingReportsList && reportsItems.length === 0 && (
                      <li className="reports-empty">Loading…</li>
                    )}
                    {!loadingReportsList && reportsItems.length === 0 && (
                      <li className="reports-empty">No reports yet</li>
                    )}
                    {reportsItems.map((item) => {
                      const isActive = selectedReportDate === item.trade_date;
                      return (
                        <li key={item.trade_date}>
                          <button
                            type="button"
                            className={`reports-item ${isActive ? "is-active" : ""}`}
                            onClick={() =>
                              void loadReportDetail(snapshot.agent.agent_id, item.trade_date)
                            }
                          >
                            <span className="reports-item-date">{item.trade_date}</span>
                            <span className="reports-item-meta">
                              {formatDateTime(item.updated_at)}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                  <div className="reports-detail">
                    {loadingReportDetail ? (
                      <div className="reports-empty">Loading report…</div>
                    ) : selectedReport ? (
                      <>
                        <div className="reports-detail-head">
                          <span className="reports-detail-date">
                            {selectedReport.trade_date}
                          </span>
                          <span className="reports-detail-meta">
                            Updated {formatDateTime(selectedReport.updated_at)}
                          </span>
                        </div>
                        <div className="reports-detail-body">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {selectedReport.content}
                          </ReactMarkdown>
                        </div>
                      </>
                    ) : (
                      <div className="reports-empty">
                        — pick a date on the left to read the report —
                      </div>
                    )}
                  </div>
                </div>
              </section>
            </>
          ) : (
            <div className="snapshot-empty">— pick a name from the roster, the books will open —</div>
          )}
        </section>
      </main>

      <footer className="board-foot">
        <span>Composed nightly · Bureau of Simulated Equities</span>
        <span>量化竞技场 · A-Share Edition</span>
      </footer>
    </div>
  );
}
