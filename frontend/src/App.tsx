import { useEffect, useState } from "react";

import { AShareApp } from "./AShareApp";
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
    tagline: "Offline paper · HK · US via OpenD",
    status: "available",
  },
];

type ArenaStatus = { slug: string; label: string; enabled: boolean };

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
  const [statuses, setStatuses] = useState<Record<string, boolean>>({});
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
      const next: Record<string, boolean> = {};
      for (const row of rows) {
        next[row.slug] = row.enabled;
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

  async function toggle(backendSlug: string, nextEnabled: boolean) {
    setBusySlug(backendSlug);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${API_BASE}/api/arenas/${backendSlug}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: nextEnabled }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
        throw new Error(body.detail ?? `HTTP ${response.status}`);
      }
      setStatuses((prev) => ({ ...prev, [backendSlug]: nextEnabled }));
      setNotice(
        `${backendSlug} marked ${nextEnabled ? "enabled" : "disabled"} in config.json. ` +
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
    ? knownArenas.filter((m) => statuses[m.backendSlug as string] === true).length
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
          const enabled = backendSlug ? statuses[backendSlug] === true : false;
          const available = m.status === "available";
          const isOpen = available && (loaded ? enabled : true);
          const dataLive = isOpen ? "true" : "false";
          const statusLabel = !available
            ? "Coming · Standby"
            : loaded
              ? enabled
                ? "Open · Trading"
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
          const toggleControl = backendSlug ? (
            <span className="market-toggle" onClick={(event) => event.stopPropagation()}>
              <button
                type="button"
                className={`toggle-button ${enabled ? "to-disable" : "to-enable"}`}
                disabled={!loaded || busySlug === backendSlug}
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  const action = enabled ? "Disable" : "Enable";
                  const verb = enabled ? "disable" : "enable";
                  const restartHint =
                    "The change is saved to config.json; the server must be restarted to take effect.";
                  const confirmed = window.confirm(
                    `${action} ${m.name}?\n\nThis will ${verb} the arena. ${restartHint}`,
                  );
                  if (!confirmed) return;
                  void toggle(backendSlug, !enabled);
                }}
                aria-label={`${enabled ? "Disable" : "Enable"} ${m.name}`}
              >
                {busySlug === backendSlug ? "…" : enabled ? "Disable" : "Enable"}
              </button>
            </span>
          ) : null;
          if (isOpen) {
            return (
              <div key={m.slug} className="market-row-wrap">
                <a className="market-row" data-live={dataLive} href={`${BASE_URL}/${m.slug}`}>
                  {inner}
                </a>
                {toggleControl}
              </div>
            );
          }
          return (
            <div key={m.slug} className="market-row-wrap">
              <div className="market-row" data-live={dataLive} aria-disabled="true">
                {inner}
              </div>
              {toggleControl}
            </div>
          );
        })}
      </section>
      <GlobalNotificationTargets />
      <div className="picker-foot">
        <span>Toggle a room to flip its enable flag · server restart applies it</span>
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
  return <MarketPicker />;
}
