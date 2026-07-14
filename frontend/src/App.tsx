import { useEffect, useState } from "react";

import { AShareApp } from "./AShareApp";
import { EODHDApp } from "./EODHDApp";
import { FutumooApp } from "./FutumooApp";
import { resolveApiBase, urlPrefix } from "./lib/api";

const BASE_URL = urlPrefix();
const API_BASE = resolveApiBase();

type Market = {
  slug: string;
  /** Backend arena slug used by `/api/arenas/<backendSlug>`. Undefined for "coming" markets. */
  backendSlug?: string;
  no: string;
  name: string;
  hanzi: string;
  tagline: string;
  status: "available" | "coming";
};

const MARKETS: Market[] = [
  {
    slug: "A-share",
    backendSlug: "ashare",
    no: "01",
    name: "A-Share",
    hanzi: "沪 · 深 · 京",
    tagline: "Akshare · Shanghai · Shenzhen · Beijing",
    status: "available",
  },
  {
    slug: "futumoo",
    backendSlug: "futumoo",
    no: "02",
    name: "Futu Moo",
    hanzi: "富途 · 离线",
    tagline: "Offline paper · HK · US · CN via OpenD",
    status: "available",
  },
  {
    slug: "eodhd",
    backendSlug: "eodhd",
    no: "03",
    name: "EODHD",
    hanzi: "全 市 场",
    tagline: "All-in-one data · CSV cache · global symbols",
    status: "available",
  },
];

type ArenaStatus = {
  slug: string;
  label: string;
  enabled: boolean;
  data_provider_only: boolean;
};

type ArenaMode = "disabled" | "data-only" | "trading";

type ArenaStatusResponse = {
  status: ArenaStatus;
  restart_required: boolean;
};

const ARENA_MODES: Record<
  ArenaMode,
  { label: string; enabled: boolean; dataProviderOnly: boolean; description: string }
> = {
  disabled: {
    label: "Disabled",
    enabled: false,
    dataProviderOnly: false,
    description: "The provider, persistence tasks, agent runtime, MCP, and trading will not start.",
  },
  "data-only": {
    label: "Data only",
    enabled: true,
    dataProviderOnly: true,
    description: "The provider and persistence tasks will start without agents, MCP, or trading.",
  },
  trading: {
    label: "Trading",
    enabled: true,
    dataProviderOnly: false,
    description: "The provider, agent runtime, MCP, and paper trading will start.",
  },
};

function arenaMode(status: ArenaStatus | undefined): ArenaMode {
  if (!status?.enabled) return "disabled";
  return status.data_provider_only ? "data-only" : "trading";
}

type NapCatPrivateTarget = { type: "private"; user_id: string };
type NapCatGroupTarget = { type: "group"; group_id: string };
type NapCatTarget = NapCatPrivateTarget | NapCatGroupTarget;

type NotificationDestinations = {
  napcat_enabled: boolean;
  napcat_destinations: Record<string, NapCatTarget>;
};

function describeNapCatTarget(target: NapCatTarget): string {
  return target.type === "private"
    ? `Private · user ${target.user_id}`
    : `Group · group ${target.group_id}`;
}

type NapCatDraft = {
  key: string;
  type: "private" | "group";
  user_id: string;
  group_id: string;
};

function GlobalNotificationTargets() {
  const [data, setData] = useState<NotificationDestinations | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>("");
  const [notice, setNotice] = useState<string>("");
  const [napcatDraft, setNapcatDraft] = useState<NapCatDraft>({
    key: "",
    type: "private",
    user_id: "",
    group_id: "",
  });
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/api/notifications/destinations`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      setData((await response.json()) as NotificationDestinations);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function saveNapCat(next: Record<string, NapCatTarget>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${API_BASE}/api/notifications/napcat/destinations`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ destinations: next }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
        throw new Error(body.detail ?? `HTTP ${response.status}`);
      }
      setData((await response.json()) as NotificationDestinations);
      setNotice("NapCat destinations saved.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function removeNapCat(key: string) {
    if (!data) return;
    if (!window.confirm(`Remove NapCat destination "${key}"?`)) return;
    const next = { ...data.napcat_destinations };
    delete next[key];
    void saveNapCat(next);
  }

  function addNapCat() {
    if (!data) return;
    const key = napcatDraft.key.trim();
    if (!key) {
      setError("NapCat destination key is required");
      return;
    }
    if (data.napcat_destinations[key]) {
      setError(`NapCat destination key "${key}" already exists`);
      return;
    }
    let target: NapCatTarget;
    if (napcatDraft.type === "private") {
      const user_id = napcatDraft.user_id.trim();
      if (!user_id) {
        setError("Private user_id is required");
        return;
      }
      target = { type: "private", user_id };
    } else {
      const group_id = napcatDraft.group_id.trim();
      if (!group_id) {
        setError("Group group_id is required");
        return;
      }
      target = { type: "group", group_id };
    }
    void saveNapCat({ ...data.napcat_destinations, [key]: target }).then(() =>
      setNapcatDraft({ key: "", type: napcatDraft.type, user_id: "", group_id: "" }),
    );
  }

  const napcatEntries = data ? Object.entries(data.napcat_destinations) : [];

  return (
    <section className="notif-section">
      <div className="section-head">
        <h3>Notification Targets</h3>
        <span className="meta">QQ destinations · NapCat</span>
      </div>
      {(error || notice) && (
        <div className={`notice ${error ? "error" : "ok"}`}>{error || notice}</div>
      )}
      <div className="notif-grid">
        <div className="notif-channel">
          <div className="notif-channel-head">
            <h4>NapCat</h4>
            <span className={`notif-channel-state ${data?.napcat_enabled ? "on" : "off"}`}>
              {loading ? "…" : data?.napcat_enabled ? "ENABLED" : "DISABLED"}
            </span>
          </div>
          <div className="notif-cards">
            {napcatEntries.length === 0 && !loading && (
              <div className="notif-empty">No NapCat destinations yet</div>
            )}
            {napcatEntries.map(([key, target]) => (
              <article key={key} className="notif-card">
                <div className="notif-card-key">{key}</div>
                <div className="notif-card-meta">{describeNapCatTarget(target)}</div>
                <button
                  type="button"
                  className="notif-card-remove"
                  onClick={() => removeNapCat(key)}
                  disabled={busy}
                  aria-label={`Remove ${key}`}
                  title="Remove destination"
                >
                  ×
                </button>
              </article>
            ))}
          </div>
          <div className="notif-add">
            <div className="notif-add-row">
              <input
                placeholder="key (alias)"
                value={napcatDraft.key}
                onChange={(event) =>
                  setNapcatDraft((prev) => ({ ...prev, key: event.target.value }))
                }
                disabled={busy}
              />
              <select
                value={napcatDraft.type}
                onChange={(event) =>
                  setNapcatDraft((prev) => ({
                    ...prev,
                    type: event.target.value as "private" | "group",
                  }))
                }
                disabled={busy}
              >
                <option value="private">private</option>
                <option value="group">group</option>
              </select>
              {napcatDraft.type === "private" ? (
                <input
                  placeholder="user_id"
                  value={napcatDraft.user_id}
                  onChange={(event) =>
                    setNapcatDraft((prev) => ({ ...prev, user_id: event.target.value }))
                  }
                  disabled={busy}
                />
              ) : (
                <input
                  placeholder="group_id"
                  value={napcatDraft.group_id}
                  onChange={(event) =>
                    setNapcatDraft((prev) => ({ ...prev, group_id: event.target.value }))
                  }
                  disabled={busy}
                />
              )}
              <button
                type="button"
                className="button"
                onClick={addNapCat}
                disabled={busy || !data}
              >
                Add
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function currentSlug(): string {
  const path = window.location.pathname;
  const stripped = BASE_URL && path.startsWith(BASE_URL) ? path.slice(BASE_URL.length) : path;
  const trimmed = stripped.replace(/^\/+|\/+$/g, "");
  if (!trimmed) {
    return "";
  }
  return trimmed.split("/")[0];
}

type Stamp = { iso: string; label: string; edition: string; weekday: string };

function todayStamp(): Stamp {
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

function MarketPicker() {
  const stamp = todayStamp();
  const [statuses, setStatuses] = useState<Record<string, ArenaStatus>>({});
  const [loaded, setLoaded] = useState(false);
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [notice, setNotice] = useState<string>("");
  const [error, setError] = useState<string>("");

  async function refreshStatuses() {
    try {
      const response = await fetch(`${API_BASE}/api/arenas`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const rows = (await response.json()) as ArenaStatus[];
      const next: Record<string, ArenaStatus> = {};
      for (const row of rows) {
        next[row.slug] = row;
      }
      setStatuses(next);
    } catch (fetchError) {
      setError((fetchError as Error).message);
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void refreshStatuses();
  }, []);

  async function setArenaMode(backendSlug: string, nextMode: ArenaMode) {
    const mode = ARENA_MODES[nextMode];
    setBusySlug(backendSlug);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${API_BASE}/api/arenas/${backendSlug}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: mode.enabled,
          data_provider_only: mode.dataProviderOnly,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
        throw new Error(body.detail ?? `HTTP ${response.status}`);
      }
      const result = (await response.json()) as ArenaStatusResponse;
      setStatuses((prev) => ({ ...prev, [backendSlug]: result.status }));
      setNotice(
        `${result.status.label} set to ${mode.label.toLowerCase()} in config.json. ` +
          "Restart the server to apply.",
      );
    } catch (toggleError) {
      setError((toggleError as Error).message);
    } finally {
      setBusySlug(null);
    }
  }

  const knownArenas = MARKETS.filter((m) => m.backendSlug !== undefined);
  const liveCount = loaded
    ? knownArenas.filter((m) => {
        const status = statuses[m.backendSlug as string];
        return status?.enabled === true && !status.data_provider_only;
      }).length
    : knownArenas.length;
  return (
    <div className="wrap reveal">
      <div className="masthead-rail">
        <span>Quant Arena · Bureau of Simulated Equities</span>
        <span>
          {stamp.weekday} · {stamp.label}
        </span>
      </div>
      <div className="rule-double" />
      <header className="masthead">
        <div>
          <h1 className="masthead-title">
            Quant <em>Arena</em>
            <span className="glyph">壹</span>
          </h1>
          <div className="masthead-han">量 化 竞 技 场</div>
        </div>
        <div className="masthead-meta">
          <span>
            <strong>VOL. I</strong> · NO. {stamp.edition}
          </span>
          <span>{stamp.iso} · UTC+08</span>
          <span>WEATHER · CLEAR · BOOKS NORMAL</span>
          <span>
            {liveCount} OF {knownArenas.length} ROOMS OPEN
          </span>
        </div>
      </header>
      <div className="rule-thick" />
      <section className="picker-intro">
        <h2>
          A trading floor where machines compete in plain sight, on paper that does not lie.
        </h2>
        <p>
          Each market is given its own room. Every agent keeps its own ledger. Positions, fills
          and commissions are recorded in full, posted live, and ranked by percentage return.
        </p>
        <p>
          Pick the room. The doors below open into the trading boards — choose carefully, the
          tape remembers.
        </p>
      </section>
      {(notice || error) && (
        <div className={`notice ${error ? "error" : "ok"}`}>{error || notice}</div>
      )}
      <div className="rule-thick" />
      <section className="picker-list">
        {MARKETS.map((m) => {
          const backendSlug = m.backendSlug;
          const arenaStatus = backendSlug ? statuses[backendSlug] : undefined;
          const enabled = arenaStatus?.enabled === true;
          const dataProviderOnly = arenaStatus?.data_provider_only === true;
          const mode = arenaMode(arenaStatus);
          const available = m.status === "available";
          const isOpen = available && (loaded ? enabled && !dataProviderOnly : true);
          const dataLive = isOpen ? "true" : "false";
          const statusLabel = !available
            ? "Coming · Standby"
            : loaded
              ? enabled
                ? dataProviderOnly
                  ? "Data Only · Persistence"
                  : "Open · Trading"
                : "Disabled · Restart to Apply"
              : "Loading…";
          const statusDot = !available || (loaded && !enabled) ? "dot-soft" : "dot-rise";
          const inner = (
            <>
              <span className="market-no">№ {m.no}</span>
              <span className="market-name-block">
                <span className="market-name">{m.name}</span>
                <span className="market-han">{m.hanzi}</span>
              </span>
              <span className="market-tagline">{m.tagline}</span>
              <span className="market-status">
                <span className={`dot ${statusDot}`} />
                {statusLabel}
              </span>
              <span className="market-cta">
                {isOpen ? (
                  <>
                    Enter<span className="arrow">→</span>
                  </>
                ) : (
                  "—"
                )}
              </span>
            </>
          );
          const modeControl = backendSlug ? (
            <span className="market-toggle" onClick={(event) => event.stopPropagation()}>
              <select
                className={`arena-mode-select mode-${mode}`}
                value={mode}
                disabled={!loaded || busySlug === backendSlug}
                onChange={(event) => {
                  event.stopPropagation();
                  const nextMode = event.target.value as ArenaMode;
                  const next = ARENA_MODES[nextMode];
                  const restartHint =
                    "The change is saved to config.json; the server must be restarted to take effect.";
                  const confirmed = window.confirm(
                    `Set ${m.name} to ${next.label}?\n\n${next.description} ${restartHint}`,
                  );
                  if (!confirmed) return;
                  void setArenaMode(backendSlug, nextMode);
                }}
                aria-label={`${m.name} arena mode`}
              >
                <option value="disabled">Disabled</option>
                <option value="data-only">Data only</option>
                <option value="trading">Trading</option>
              </select>
            </span>
          ) : null;
          if (isOpen) {
            return (
              <div key={m.slug} className="market-row-wrap">
                <a className="market-row" data-live={dataLive} href={`${BASE_URL}/${m.slug}`}>
                  {inner}
                </a>
                {modeControl}
              </div>
            );
          }
          return (
            <div key={m.slug} className="market-row-wrap">
              <div className="market-row" data-live={dataLive} aria-disabled="true">
                {inner}
              </div>
              {modeControl}
            </div>
          );
        })}
      </section>
      <GlobalNotificationTargets />
      <div className="picker-foot">
        <span>Choose disabled, data only, or trading · server restart applies it</span>
        <span>Composed in {stamp.label.split(",")[0]} · 量化竞技场</span>
      </div>
    </div>
  );
}

export function App() {
  const slug = currentSlug();
  if (slug === "A-share") {
    return <AShareApp />;
  }
  if (slug === "futumoo") {
    return <FutumooApp />;
  }
  if (slug === "eodhd") {
    return <EODHDApp />;
  }
  return <MarketPicker />;
}
