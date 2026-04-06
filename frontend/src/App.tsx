import { FormEvent, useEffect, useState } from "react";

type PathsResponse = {
  config_path: string;
  project_root: string;
  market_data_root: string;
  agents_config_path: string;
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
  const [draft, setDraft] = useState<AgentDraft>(EMPTY_DRAFT);
  const [error, setError] = useState<string>("");
  const [isSaving, setIsSaving] = useState(false);

  async function load(): Promise<void> {
    const [nextPaths, nextRankings, nextAgents] = await Promise.all([
      fetchJson<PathsResponse>("/api/paths"),
      fetchJson<RankingEntry[]>("/api/rankings"),
      fetchJson<Agent[]>("/api/agents"),
    ]);
    setPaths(nextPaths);
    setRankings(nextRankings);
    setAgents(nextAgents);
  }

  useEffect(() => {
    void load().catch((loadError: Error) => {
      setError(loadError.message);
    });
  }, []);

  async function handleRefreshMarket(): Promise<void> {
    setError("");
    try {
      await fetchJson<{ status: string }>("/api/market/refresh", { method: "POST" });
      await load();
    } catch (refreshError) {
      setError((refreshError as Error).message);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setIsSaving(true);
    setError("");
    try {
      await fetchJson<Agent>("/api/agents", {
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

  return (
    <div className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Quant Arena</p>
          <h1>Agent trading monitor</h1>
          <p className="subhead">
            Frontend now lives outside the Python package. Public market cache and private project data stay on
            separate roots.
          </p>
        </div>
        <button type="button" onClick={() => void handleRefreshMarket()}>
          Refresh market
        </button>
      </header>

      {error ? <div className="banner error">{error}</div> : null}

      <main className="grid">
        <section className="panel">
          <div className="panel-head">
            <h2>Paths</h2>
          </div>
          <pre>{paths ? JSON.stringify(paths, null, 2) : "Loading..."}</pre>
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
                    <td>{entry.total_equity.toFixed(2)}</td>
                    <td>{entry.return_pct.toFixed(2)}%</td>
                    <td>{entry.realized_pnl.toFixed(2)}</td>
                    <td>{entry.unrealized_pnl.toFixed(2)}</td>
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
                  Initial cash: {agent.initial_cash.toFixed(2)}
                  <br />
                  Token header: <code>{agent.token_header_name}</code>
                </li>
              ))
            ) : (
              <li>No agents registered.</li>
            )}
          </ul>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Create agent</h2>
          </div>
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
