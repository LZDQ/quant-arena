import { startTransition, useEffect, useState } from "react";

type AgentResponse = {
  agent_id: string;
  display_name: string;
  initial_cash: number;
  sell_constraint: string;
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

const API_BASE = import.meta.env.VITE_API_BASE ?? import.meta.env.BASE_URL.replace(/\/$/, "");

const defaultCreateAgentForm: CreateAgentForm = {
  agent_id: "",
  display_name: "",
  initial_cash: "100000",
  role: "normal",
};

const ORDERS_PAGE_SIZE = 8;

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

function percentClass(value: number): string {
  if (value > 0) {
    return "up";
  }
  if (value < 0) {
    return "down";
  }
  return "flat";
}

function tinyEquityPath(points: EquityPoint[]): string {
  if (points.length < 2) {
    return "";
  }
  const values = points.map((point) => point.total_equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  return points
    .map((point, index) => {
      const x = (index / (points.length - 1)) * 100;
      const y = 100 - ((point.total_equity - min) / range) * 100;
      return `${x},${y}`;
    })
    .join(" ");
}

export function App() {
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
      return;
    }
    setOrdersPage(1);
    void refreshSnapshot(selectedAgentId);
  }, [selectedAgentId]);

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

  const latestEquity = snapshot && snapshot.equity.length > 0 ? snapshot.equity[snapshot.equity.length - 1] : null;
  const equityPolyline = snapshot ? tinyEquityPath(snapshot.equity) : "";
  const agentById = new Map(agents.map((agent) => [agent.agent_id, agent]));
  const orderedOrders = snapshot ? [...snapshot.operations.orders].reverse() : [];
  const fillByOrderId = new Map((snapshot?.operations.fills ?? []).map((fill) => [fill.order_id, fill]));
  const totalOrdersPages = Math.max(1, Math.ceil(orderedOrders.length / ORDERS_PAGE_SIZE));
  const currentOrdersPage = Math.min(ordersPage, totalOrdersPages);
  const visibleOrders = orderedOrders.slice(
    (currentOrdersPage - 1) * ORDERS_PAGE_SIZE,
    currentOrdersPage * ORDERS_PAGE_SIZE,
  );

  return (
    <div className="app-shell">
      <header className="hero-bar">
        <div>
          <p className="eyebrow">QUANT ARENA</p>
          <h1>RED TAPE TRADING BOARD</h1>
          <p className="hero-copy">
            Bold live oversight for agents, positions, equity curve and market maintenance.
          </p>
        </div>
      </header>

      {(message || error) && (
        <section className={`status-ribbon ${error ? "status-ribbon-error" : "status-ribbon-ok"}`}>
          {error || message}
        </section>
      )}

      <main className="dashboard-grid">
        <section className="panel panel-agent-list">
          <div className="panel-header">
            <div>
              <p className="panel-kicker">Agents</p>
              <h2>Battle Line</h2>
            </div>
            <span className="panel-chip">{loadingAgents || loadingRankings ? "Updating" : `${rankings.length} ranked`}</span>
          </div>

          <div className="agent-list">
            {rankings.map((entry, index) => {
              const agent = agentById.get(entry.agent_id);
              const isActive = selectedAgentId === entry.agent_id;
              return (
                <button
                  key={entry.agent_id}
                  className={`agent-card ${isActive ? "agent-card-active" : ""}`}
                  onClick={() => setSelectedAgentId(entry.agent_id)}
                >
                  <div className="agent-card-identity">
                    <div className="agent-card-title">
                      #{String(index + 1).padStart(2, "0")} {entry.display_name}
                    </div>
                    <div className="agent-card-subtitle">{entry.agent_id}</div>
                  </div>
                  <div className="agent-card-center">
                    <div className="ranking-breakdown">
                      <span className="ranking-breakdown-label">Cash</span>
                      <strong>{formatMoney(entry.cash)}</strong>
                    </div>
                    <div className="ranking-breakdown">
                      <span className="ranking-breakdown-label">Market Value</span>
                      <strong>{formatMoney(entry.market_value)}</strong>
                    </div>
                  </div>
                  <div className="agent-card-side">
                    <div className="ranking-metrics">
                      <strong>{formatMoney(entry.total_equity)}</strong>
                      <span className={percentClass(entry.return_pct)}>{formatNumber(entry.return_pct, 2)}%</span>
                    </div>
                    {agent && (
                      <>
                        <span className={`agent-pill ${agent.enabled ? "agent-pill-live" : "agent-pill-off"}`}>
                          {agent.enabled ? "LIVE" : "OFF"}
                        </span>
                        <span className="agent-card-subtitle">{agent.role.toUpperCase()}</span>
                      </>
                    )}
                  </div>
                </button>
              );
            })}
            {!loadingRankings && rankings.length === 0 && <p className="empty-copy">No rankings yet.</p>}
          </div>

          <form className="create-agent-form" onSubmit={handleCreateAgent}>
            <div className="panel-header compact">
              <div>
                <p className="panel-kicker">Create</p>
                <h3>Deploy New Agent</h3>
              </div>
            </div>
            <div className="create-agent-grid">
              <input
                value={createAgentForm.agent_id}
                onChange={(event) => setCreateAgentForm((prev) => ({ ...prev, agent_id: event.target.value }))}
                placeholder="agent_id"
                required
              />
              <input
                value={createAgentForm.initial_cash}
                onChange={(event) => setCreateAgentForm((prev) => ({ ...prev, initial_cash: event.target.value }))}
                placeholder="Initial cash"
                type="number"
                min="1"
                required
              />
              <input
                value={createAgentForm.display_name}
                onChange={(event) => setCreateAgentForm((prev) => ({ ...prev, display_name: event.target.value }))}
                placeholder="Display name"
                required
              />
              <div
                className="select-wrap"
                onBlur={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                    setModeMenuOpen(false);
                  }
                }}
              >
                <button
                  className="form-select select-trigger"
                  type="button"
                  aria-haspopup="listbox"
                  aria-expanded={modeMenuOpen}
                  onClick={() => setModeMenuOpen((open) => !open)}
                >
                  {createAgentForm.role === "normal" ? "Normal" : "Monitor"}
                </button>
                {modeMenuOpen && (
                  <div className="select-menu" role="listbox" aria-label="Agent mode">
                    <button
                      className={`select-option ${createAgentForm.role === "normal" ? "select-option-active" : ""}`}
                      type="button"
                      onClick={() => {
                        setCreateAgentForm((prev) => ({ ...prev, role: "normal" }));
                        setModeMenuOpen(false);
                      }}
                    >
                      Normal
                    </button>
                    <button
                      className={`select-option ${createAgentForm.role === "monitor" ? "select-option-active" : ""}`}
                      type="button"
                      onClick={() => {
                        setCreateAgentForm((prev) => ({ ...prev, role: "monitor" }));
                        setModeMenuOpen(false);
                      }}
                    >
                      Monitor
                    </button>
                  </div>
                )}
              </div>
            </div>
            <button className="action-button action-button-solid" type="submit">
              Create Agent
            </button>
            {createdToken && (
              <div className="token-card">
                <div className="token-card-label">Copy This Token For {createdAgentId}</div>
                <div className="token-card-value">{createdToken}</div>
                <button
                  className="action-button"
                  type="button"
                  onClick={() => void navigator.clipboard.writeText(createdToken)}
                >
                  Copy Token
                </button>
              </div>
            )}
          </form>
        </section>

        <section className="panel panel-main">
          <div className="panel-header">
                <div>
                  <p className="panel-kicker">Snapshot</p>
                  <h2>{snapshot?.agent.display_name ?? "Select an Agent"}</h2>
                  {snapshot && <p className="agent-card-subtitle">{snapshot.agent.agent_id} · {snapshot.agent.role.toUpperCase()}</p>}
                </div>
            {snapshot && (
              <button className="destructive-link" onClick={() => void handleDeleteAgent(snapshot.agent.agent_id)}>
                Remove Agent
              </button>
            )}
          </div>

          {loadingSnapshot ? (
            <div className="empty-copy">Loading snapshot...</div>
          ) : snapshot ? (
            <>
              <section className="stat-rack">
                <article className="stat-tile">
                  <span className="tile-label">Total Equity</span>
                  <strong>{formatMoney(snapshot.portfolio.total_equity)}</strong>
                </article>
                <article className="stat-tile">
                  <span className="tile-label">Cash</span>
                  <strong>{formatMoney(snapshot.portfolio.cash)}</strong>
                </article>
                <article className="stat-tile">
                  <span className="tile-label">Market Value</span>
                  <strong>{formatMoney(snapshot.portfolio.market_value)}</strong>
                </article>
                <article className="stat-tile">
                  <span className="tile-label">As Of</span>
                  <strong>{formatDateTime(snapshot.portfolio.as_of)}</strong>
                </article>
              </section>

              <section className="equity-strip">
                <div>
                  <p className="panel-kicker">Equity</p>
                  <h3>{latestEquity ? formatMoney(latestEquity.total_equity) : "--"}</h3>
                  <p className={`curve-meta ${percentClass(rankings.find((entry) => entry.agent_id === snapshot.agent.agent_id)?.return_pct ?? 0)}`}>
                    Return {formatNumber(rankings.find((entry) => entry.agent_id === snapshot.agent.agent_id)?.return_pct ?? 0, 2)}%
                  </p>
                </div>
                <div className="curve-box">
                  {equityPolyline ? (
                    <svg viewBox="0 0 100 100" preserveAspectRatio="none">
                      <polyline points={equityPolyline} />
                    </svg>
                  ) : (
                    <div className="curve-placeholder">Need at least two equity points</div>
                  )}
                </div>
              </section>

              <div className="subgrid">
                <section className="table-panel">
                  <div className="panel-header compact">
                    <div>
                      <p className="panel-kicker">Holdings</p>
                      <h3>Positions</h3>
                    </div>
                    <span className="panel-chip">{snapshot.portfolio.positions.length}</span>
                  </div>
                  <table>
                    <thead>
                      <tr>
                        <th>Code</th>
                        <th>Qty</th>
                        <th>Sellable</th>
                        <th>Avg</th>
                        <th>Last</th>
                        <th>Value</th>
                        <th>PnL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {snapshot.portfolio.positions.map((position) => (
                        <tr key={position.code}>
                          <td>{position.code}</td>
                          <td>{position.quantity}</td>
                          <td>{position.sellable_quantity}</td>
                          <td>{formatNumber(position.avg_cost, 3)}</td>
                          <td>{formatNumber(position.market_price, 3)}</td>
                          <td>{formatMoney(position.market_value)}</td>
                          <td className={percentClass(position.unrealized_pnl)}>{formatMoney(position.unrealized_pnl)}</td>
                        </tr>
                      ))}
                      {snapshot.portfolio.positions.length === 0 && (
                        <tr>
                          <td colSpan={7} className="empty-table">No positions.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </section>

                <section className="table-panel">
                  <div className="panel-header compact">
                    <div>
                      <p className="panel-kicker">Orders</p>
                      <h3>Pending + Recent</h3>
                    </div>
                    <div className="table-panel-tools">
                      <span className="panel-chip">{snapshot.operations.orders.length}</span>
                      {snapshot.operations.orders.length > 0 && (
                        <div className="table-pager" aria-label="Orders pagination">
                          <button
                            className="pager-button"
                            type="button"
                            onClick={() => setOrdersPage((page) => Math.max(1, page - 1))}
                            disabled={currentOrdersPage === 1}
                          >
                            Prev Page
                          </button>
                          <span className="pager-label">
                            Page {currentOrdersPage}/{totalOrdersPages}
                          </span>
                          <button
                            className="pager-button"
                            type="button"
                            onClick={() => setOrdersPage((page) => Math.min(totalOrdersPages, page + 1))}
                            disabled={currentOrdersPage === totalOrdersPages}
                          >
                            Next Page
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
                        <th>Qty</th>
                        <th>Limit</th>
                        <th>Filled</th>
                        <th>Status</th>
                        <th>Comment</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleOrders.map((order) => {
                        const fill = fillByOrderId.get(order.order_id);
                        return (
                          <tr key={order.order_id}>
                            <td>{formatDateTime(order.submitted_at)}</td>
                            <td>{order.code}</td>
                            <td className={order.side === "buy" ? "up" : "down"}>{order.side.toUpperCase()}</td>
                            <td>{order.quantity}</td>
                            <td>{formatNumber(order.limit_price, 2)}</td>
                            <td>{fill ? formatNumber(fill.executed_price, 2) : "--"}</td>
                            <td>
                              {order.filled_at ? (
                                <div>Filled {formatDateTime(order.filled_at)}</div>
                              ) : order.canceled_at ? (
                                <div>Canceled {formatDateTime(order.canceled_at)}</div>
                              ) : (
                                <div>{order.status}</div>
                              )}
                              {order.rejection_reason && <div className="order-meta down">{order.rejection_reason}</div>}
                            </td>
                            <td className="comment-cell">{order.comment}</td>
                          </tr>
                        );
                      })}
                      {snapshot.operations.orders.length === 0 && (
                        <tr>
                          <td colSpan={8} className="empty-table">No order history.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </section>
              </div>
            </>
          ) : null}
        </section>

      </main>
    </div>
  );
}
