import { ReactNode, startTransition, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Currency = "CNY" | "HKD" | "USD";

type AgentResponse = {
  agent_id: string;
  display_name: string;
  initial_cash: number;
  currency: Currency;
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
  currency: Currency;
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
  currency: Currency;
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
  currency: Currency;
  role: "normal" | "monitor";
};

const ORDERS_PAGE_SIZE = 8;
const REPORTS_PAGE_SIZE = 100;
const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function makeDefaultCreateAgentForm(currency: Currency): CreateAgentForm {
  return {
    agent_id: "",
    display_name: "",
    initial_cash: "100000",
    currency,
    role: "normal",
  };
}

function pad2(value: number): string {
  return value.toString().padStart(2, "0");
}

function formatDateKey(year: number, month: number, day: number): string {
  return `${year}-${pad2(month + 1)}-${pad2(day)}`;
}

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

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value == null) {
    return "--";
  }
  return value.toFixed(digits);
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

export type CurrencyOption = {
  /** Backend currency code, e.g. "CNY", "HKD", "USD". */
  value: Currency;
  /** Label shown in the form (e.g. "RMB" while value="CNY"). */
  label: string;
};

export type ArenaDashboardProps = {
  /** Path prefix for the per-arena REST API, e.g. "" or "/futumoo". */
  apiPrefix: string;
  /** URL of the home / market-picker page. Used by the "← All Markets" link. */
  homeUrl: string;
  /**
   * Number formatter for cash / equity / market values. Receives the relevant
   * agent's currency so per-arena formatters can switch glyphs (e.g. ¥ vs HK$
   * vs $).
   */
  formatAmount: (value: number | null | undefined, currency: Currency) => string;
  /** Y-axis label formatter for the equity chart, currency-aware. */
  formatYAxisLabel: (value: number, currency: Currency) => string;
  /** ISO datetime → display string. Differs by arena timezone. */
  formatDateTime: (value: string | null | undefined) => string;
  /** Masthead block. */
  masthead: {
    title: ReactNode;
    glyph: string;
    han: string;
    metaLines: string[];
  };
  /** Holdings table column header for the symbol/code column. */
  symbolHeader: string;
  /** Placeholders for the enlist form. */
  enlistPlaceholders: {
    agentId: string;
    displayName: string;
  };
  /** Confirmation prefix when deleting an agent (e.g. "Delete futumoo agent"). */
  confirmDeletePrefix: string;
  /**
   * Currencies the user may pick when enlisting an agent. A single-option list
   * locks the picker; a multi-option list renders a dropdown. The first option
   * is the default selection.
   */
  currencyOptions: CurrencyOption[];
  /** Footer text (left + right halves). */
  footer: {
    left: string;
    right: string;
  };
};

export function ArenaDashboard({
  apiPrefix,
  homeUrl,
  formatAmount,
  formatYAxisLabel,
  formatDateTime,
  masthead,
  symbolHeader,
  enlistPlaceholders,
  confirmDeletePrefix,
  currencyOptions,
  footer,
}: ArenaDashboardProps) {
  const apiBase = (import.meta.env.VITE_API_BASE ?? homeUrl).replace(/\/+$/, "");
  const defaultCurrency = currencyOptions[0]?.value ?? "CNY";
  const currencyLocked = currencyOptions.length <= 1;
  const currencyLabel = (value: Currency): string =>
    currencyOptions.find((option) => option.value === value)?.label ?? value;

  async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${apiBase}${path}`, {
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

  const [agents, setAgents] = useState<AgentResponse[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string>("");
  const [snapshot, setSnapshot] = useState<AgentSnapshotResponse | null>(null);
  const [rankings, setRankings] = useState<RankingEntry[]>([]);
  const [createAgentForm, setCreateAgentForm] = useState<CreateAgentForm>(() =>
    makeDefaultCreateAgentForm(defaultCurrency),
  );
  const [currencyMenuOpen, setCurrencyMenuOpen] = useState(false);
  const [loadingAgents, setLoadingAgents] = useState(true);
  const [loadingSnapshot, setLoadingSnapshot] = useState(false);
  const [loadingRankings, setLoadingRankings] = useState(true);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [createdToken, setCreatedToken] = useState<string>("");
  const [createdAgentId, setCreatedAgentId] = useState<string>("");
  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const [ordersPage, setOrdersPage] = useState(1);
  const [reportsList, setReportsList] = useState<DailyReportPage | null>(null);
  const [loadingReportsList, setLoadingReportsList] = useState(false);
  const [selectedReportDate, setSelectedReportDate] = useState<string>("");
  const [selectedReport, setSelectedReport] = useState<DailyReport | null>(null);
  const [loadingReportDetail, setLoadingReportDetail] = useState(false);
  const [calendarMonth, setCalendarMonth] = useState<{ year: number; month: number }>(() => {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() };
  });

  useEffect(() => {
    void refreshAgents(getAgentIdFromUrl());
    void refreshRankings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    setSelectedReport(null);
    setSelectedReportDate("");
    const now = new Date();
    setCalendarMonth({ year: now.getFullYear(), month: now.getMonth() });
    void refreshSnapshot(selectedAgentId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAgentId]);

  useEffect(() => {
    if (!selectedAgentId) {
      return;
    }
    void refreshReports(selectedAgentId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAgentId]);

  async function refreshAgents(preferredAgentId?: string) {
    setLoadingAgents(true);
    setError("");
    try {
      const data = await apiFetch<AgentResponse[]>(`/api${apiPrefix}/agents`);
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
      const data = await apiFetch<AgentSnapshotResponse>(`/api${apiPrefix}/agents/${agentId}`);
      setSnapshot(data);
    } catch (fetchError) {
      setError((fetchError as Error).message);
      setSnapshot(null);
    } finally {
      setLoadingSnapshot(false);
    }
  }

  async function refreshReports(agentId: string) {
    setLoadingReportsList(true);
    try {
      const data = await apiFetch<DailyReportPage>(
        `/api${apiPrefix}/agents/${agentId}/daily-reports?page=1&page_size=${REPORTS_PAGE_SIZE}`,
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
        `/api${apiPrefix}/agents/${agentId}/daily-reports/${tradeDate}`,
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
      const data = await apiFetch<RankingEntry[]>(`/api${apiPrefix}/rankings`);
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
      const created = await apiFetch<AgentCreatedResponse>(`/api${apiPrefix}/agents`, {
        method: "POST",
        body: JSON.stringify({
          ...createAgentForm,
          initial_cash: Number(createAgentForm.initial_cash),
        }),
      });
      setCreatedToken(created.token_secret);
      setCreatedAgentId(created.agent.agent_id);
      setCreateAgentForm(makeDefaultCreateAgentForm(defaultCurrency));
      setMessage(`Agent ${created.agent.agent_id} created.`);
      await refreshAgents(created.agent.agent_id);
      await refreshRankings();
    } catch (fetchError) {
      setError((fetchError as Error).message);
    }
  }

  async function handleDeleteAgent(agentId: string) {
    const confirmed = window.confirm(`${confirmDeletePrefix} ${agentId}?`);
    if (!confirmed) {
      return;
    }
    setMessage("");
    setError("");
    setCreatedToken("");
    setCreatedAgentId("");
    try {
      await apiFetch<void>(`/api${apiPrefix}/agents/${agentId}`, { method: "DELETE" });
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
  const reportsItems = reportsList?.items ?? [];
  const reportsByDate = useMemo(() => {
    const map = new Map<string, DailyReportSummary>();
    for (const item of reportsItems) {
      map.set(item.trade_date, item);
    }
    return map;
  }, [reportsItems]);
  const calendarCells = useMemo(() => {
    const { year, month } = calendarMonth;
    const firstWeekday = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const cells: ({ key: string; day: number } | null)[] = [];
    for (let i = 0; i < firstWeekday; i += 1) {
      cells.push(null);
    }
    for (let day = 1; day <= daysInMonth; day += 1) {
      cells.push({ key: formatDateKey(year, month, day), day });
    }
    while (cells.length % 7 !== 0) {
      cells.push(null);
    }
    return cells;
  }, [calendarMonth]);
  const todayKey = useMemo(() => {
    const now = new Date();
    return formatDateKey(now.getFullYear(), now.getMonth(), now.getDate());
  }, []);
  const calendarMonthLabel = `${calendarMonth.year}-${pad2(calendarMonth.month + 1)}`;
  function shiftCalendarMonth(delta: number) {
    setCalendarMonth(({ year, month }) => {
      const next = new Date(year, month + delta, 1);
      return { year: next.getFullYear(), month: next.getMonth() };
    });
  }

  return (
    <div className="wrap reveal">
      <div className="masthead-rail">
        <a className="home-link" href={`${homeUrl}/`}>
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
            {masthead.title}
            <span className="glyph">{masthead.glyph}</span>
          </h1>
          <div className="masthead-han">{masthead.han}</div>
        </div>
        <div className="masthead-meta">
          {masthead.metaLines.map((line) => (
            <span key={line}>{line}</span>
          ))}
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
            <span className="meta">Ranked · Return %</span>
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
                  data-currency={entry.currency}
                  onClick={() => setSelectedAgentId(entry.agent_id)}
                >
                  <span className="roster-rank">{String(index + 1).padStart(2, "0")}</span>
                  <span>
                    <div className="roster-name">{entry.display_name}</div>
                    <div className="roster-id">{entry.agent_id}</div>
                    <span className="roster-meta-row" style={{ marginTop: 8, display: "inline-flex", gap: 6 }}>
                      <span className={`roster-pill currency currency-${entry.currency}`}>
                        {entry.currency}
                      </span>
                      {agent && (
                        <span className={`roster-pill ${agent.enabled ? "live" : ""}`}>
                          {agent.enabled ? "LIVE" : "OFF"} · {agent.role.toUpperCase()}
                        </span>
                      )}
                    </span>
                  </span>
                  <span className="roster-stats">
                    <span className="roster-equity">{formatAmount(entry.total_equity, entry.currency)}</span>
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
                  placeholder={enlistPlaceholders.agentId}
                  required
                />
              </div>
              <div className="field field-half">
                <label htmlFor="initial_cash">Initial Cash · {currencyLabel(createAgentForm.currency)}</label>
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
              <div className="field field-half">
                <label htmlFor="display_name">Display Name</label>
                <input
                  id="display_name"
                  value={createAgentForm.display_name}
                  onChange={(event) =>
                    setCreateAgentForm((prev) => ({ ...prev, display_name: event.target.value }))
                  }
                  placeholder={enlistPlaceholders.displayName}
                  required
                />
              </div>
              <div
                className="field field-half select-wrap"
                onBlur={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                    setCurrencyMenuOpen(false);
                  }
                }}
              >
                <label>Currency</label>
                {currencyLocked ? (
                  <button className="select-trigger" type="button" disabled aria-disabled="true">
                    <span>{currencyLabel(createAgentForm.currency)}</span>
                  </button>
                ) : (
                  <>
                    <button
                      className="select-trigger"
                      type="button"
                      aria-haspopup="listbox"
                      aria-expanded={currencyMenuOpen}
                      onClick={() => setCurrencyMenuOpen((open) => !open)}
                    >
                      <span>{currencyLabel(createAgentForm.currency)}</span>
                    </button>
                    {currencyMenuOpen && (
                      <div className="select-menu" role="listbox" aria-label="Trading currency">
                        {currencyOptions.map((option) => (
                          <button
                            key={option.value}
                            className={`select-option ${createAgentForm.currency === option.value ? "is-active" : ""}`}
                            type="button"
                            onClick={() => {
                              setCreateAgentForm((prev) => ({ ...prev, currency: option.value }));
                              setCurrencyMenuOpen(false);
                            }}
                          >
                            {option.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                )}
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
                  <p className="label">Total Equity · {snapshot.agent.currency}</p>
                  <div className="value">{formatAmount(snapshot.portfolio.total_equity, snapshot.agent.currency)}</div>
                </article>
                <article className="stat-tile">
                  <p className="label">Cash</p>
                  <div className="value">{formatAmount(snapshot.portfolio.cash, snapshot.agent.currency)}</div>
                </article>
                <article className="stat-tile">
                  <p className="label">Market Value</p>
                  <div className="value">{formatAmount(snapshot.portfolio.market_value, snapshot.agent.currency)}</div>
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
                    {latestEquity ? formatAmount(latestEquity.total_equity, snapshot.agent.currency) : "--"}
                  </h3>
                  <p className={`return ${percentClass(selectedRanking?.return_pct ?? 0)}`}>
                    Return {signedPct(selectedRanking?.return_pct ?? 0)}
                  </p>
                  {chart && (
                    <p className="span">
                      {formatDateShort(chart.first)} → {formatDateShort(chart.last)}
                      <br />
                      Range {formatAmount(chart.min, snapshot.agent.currency)} – {formatAmount(chart.max, snapshot.agent.currency)}
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
                        {formatYAxisLabel(chart.gridY[0].value, snapshot.agent.currency)}
                      </text>
                      <text
                        className="axis-label"
                        x={chart.W - chart.P}
                        y={chart.gridY[chart.gridY.length - 1].y + 14}
                        textAnchor="end"
                      >
                        {formatYAxisLabel(chart.gridY[chart.gridY.length - 1].value, snapshot.agent.currency)}
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
                      <th>{symbolHeader}</th>
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
                        <td className="num">{formatAmount(position.market_value, snapshot.agent.currency)}</td>
                        <td className={`num ${percentClass(position.unrealized_pnl)}`}>
                          {formatAmount(position.unrealized_pnl, snapshot.agent.currency)}
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
                      <th>{symbolHeader}</th>
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
                  </div>
                </div>
                <div className="reports">
                  <div className="reports-calendar">
                    <div className="calendar-head">
                      <button
                        type="button"
                        className="calendar-nav"
                        onClick={() => shiftCalendarMonth(-1)}
                        aria-label="Previous month"
                      >
                        ←
                      </button>
                      <span className="calendar-title">{calendarMonthLabel}</span>
                      <button
                        type="button"
                        className="calendar-nav"
                        onClick={() => shiftCalendarMonth(1)}
                        aria-label="Next month"
                      >
                        →
                      </button>
                    </div>
                    <div className="calendar-grid">
                      {WEEKDAY_LABELS.map((label) => (
                        <span key={label} className="calendar-dow">
                          {label}
                        </span>
                      ))}
                      {calendarCells.map((cell, idx) => {
                        if (!cell) {
                          return <span key={`blank-${idx}`} className="calendar-cell is-blank" />;
                        }
                        const hasReport = reportsByDate.has(cell.key);
                        const isActive = selectedReportDate === cell.key;
                        const isToday = cell.key === todayKey;
                        const classes = [
                          "calendar-cell",
                          hasReport ? "has-report" : "no-report",
                          isActive ? "is-active" : "",
                          isToday ? "is-today" : "",
                        ]
                          .filter(Boolean)
                          .join(" ");
                        return (
                          <button
                            key={cell.key}
                            type="button"
                            className={classes}
                            disabled={!hasReport || loadingReportDetail}
                            onClick={() =>
                              void loadReportDetail(snapshot.agent.agent_id, cell.key)
                            }
                          >
                            {cell.day}
                          </button>
                        );
                      })}
                    </div>
                    <div className="calendar-meta">
                      {loadingReportsList
                        ? "Loading…"
                        : reportsTotal === 0
                          ? "No reports yet"
                          : "· filled days have reports ·"}
                    </div>
                  </div>
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
                        — pick a date on the calendar to read the report —
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
        <span>{footer.left}</span>
        <span>{footer.right}</span>
      </footer>
    </div>
  );
}
