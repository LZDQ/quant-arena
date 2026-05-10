import { useEffect, useState } from "react";

import { AShareApp } from "./AShareApp";
import { FutumooApp } from "./FutumooApp";
import { IBApp } from "./IBApp";

const BASE_URL = import.meta.env.BASE_URL.replace(/\/+$/, "");
const API_BASE = (import.meta.env.VITE_API_BASE ?? BASE_URL).replace(/\/+$/, "");

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
  {
    slug: "ib",
    backendSlug: "ib",
    no: "03",
    name: "Interactive Brokers",
    hanzi: "盈 透",
    tagline: "Online paper · Real · Gateway",
    status: "available",
  },
];

type ArenaStatus = { slug: string; label: string; enabled: boolean };

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
  if (slug === "ib") {
    return <IBApp />;
  }
  return <MarketPicker />;
}
