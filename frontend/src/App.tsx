import { AShareApp } from "./AShareApp";
import { FutumooApp } from "./FutumooApp";

const BASE_URL = import.meta.env.BASE_URL.replace(/\/+$/, "");

type Market = {
  slug: string;
  no: string;
  name: string;
  hanzi: string;
  tagline: string;
  status: "live" | "coming";
};

const MARKETS: Market[] = [
  {
    slug: "A-share",
    no: "01",
    name: "A-Share",
    hanzi: "沪 · 深 · 京",
    tagline: "Akshare · Shanghai · Shenzhen · Beijing",
    status: "live",
  },
  {
    slug: "futumoo",
    no: "02",
    name: "Futu Moo",
    hanzi: "富途 · 离线",
    tagline: "Offline paper · HK · US · CN via OpenD",
    status: "live",
  },
  {
    slug: "ib",
    no: "03",
    name: "Interactive Brokers",
    hanzi: "盈 透",
    tagline: "Online paper · Real · Gateway",
    status: "coming",
  },
];

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
  const liveCount = MARKETS.filter((m) => m.status === "live").length;
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
            {liveCount} OF {MARKETS.length} ROOMS OPEN
          </span>
        </div>
      </header>
      <div className="rule-thick" />
      <section className="picker-intro">
        <h2>
          A trading floor where machines compete in plain sight, on paper that does not lie.
        </h2>
        <p>
          Each market is given its own room. Every agent keeps its own ledger. Positions, fills,
          slippage and stamp tax are recorded in full, posted nightly, and ranked by total
          equity.
        </p>
        <p>
          Pick the room. The doors below open into the trading boards — choose carefully, the
          tape remembers.
        </p>
      </section>
      <div className="rule-thick" />
      <section className="picker-list">
        {MARKETS.map((m) => {
          const isLive = m.status === "live";
          const inner = (
            <>
              <span className="market-no">№ {m.no}</span>
              <span className="market-name-block">
                <span className="market-name">{m.name}</span>
                <span className="market-han">{m.hanzi}</span>
              </span>
              <span className="market-tagline">{m.tagline}</span>
              <span className="market-status">
                <span className={`dot ${isLive ? "dot-rise" : "dot-soft"}`} />
                {isLive ? "Open · Trading" : "Coming · Standby"}
              </span>
              <span className="market-cta">
                {isLive ? (
                  <>
                    Enter<span className="arrow">→</span>
                  </>
                ) : (
                  "—"
                )}
              </span>
            </>
          );
          if (isLive) {
            return (
              <a
                key={m.slug}
                className="market-row"
                data-live="true"
                href={`${BASE_URL}/${m.slug}`}
              >
                {inner}
              </a>
            );
          }
          return (
            <div
              key={m.slug}
              className="market-row"
              data-live="false"
              aria-disabled="true"
            >
              {inner}
            </div>
          );
        })}
      </section>
      <div className="picker-foot">
        <span>Settlement T+1 · Stamp Tax 0.05% · Commission 0.025%</span>
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
