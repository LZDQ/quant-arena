import { useEffect, useMemo, useState } from "react";

import { EnlistForm } from "./components/arena/EnlistForm";
import { Leaderboard } from "./components/arena/Leaderboard";
import { ManualResetModal } from "./components/arena/ManualResetModal";
import { ReportsSection } from "./components/arena/ReportsSection";
import { Roster } from "./components/arena/Roster";
import { SnapshotPanel } from "./components/arena/SnapshotPanel";
import { useArena } from "./hooks/useArena";
import { useDailyReports } from "./hooks/useDailyReports";
import { useLeaderboardCurves } from "./hooks/useLeaderboardCurves";
import { usePersistentToggle } from "./hooks/usePersistentToggle";
import { createArenaApi, urlPrefix } from "./lib/api";
import { todayStamp } from "./lib/format";
import type { ArenaCurrency, Currency, EODHDUserInfo } from "./lib/types";

const BASE_URL = urlPrefix();

const currencyFormatters: Record<Currency, Intl.NumberFormat> = {
  HKD: new Intl.NumberFormat("en-HK", {
    style: "currency",
    currency: "HKD",
    maximumFractionDigits: 2,
  }),
  USD: new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }),
  CNY: new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 2,
  }),
};

const currencyGlyph: Record<Currency, string> = {
  HKD: "HK$",
  USD: "$",
  CNY: "¥",
};

function formatAmount(value: number | null | undefined, currency: ArenaCurrency): string {
  if (value == null) {
    return "--";
  }
  if (!currency) {
    return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
  return currencyFormatters[currency].format(value);
}

const utcDatetimeFormatter = new Intl.DateTimeFormat("en-US", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});

const utcTimeFormatter = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return utcDatetimeFormatter.format(new Date(value));
}

function formatTime(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return utcTimeFormatter.format(new Date(value));
}

function formatYAxisLabel(value: number, currency: ArenaCurrency): string {
  const glyph = currency ? currencyGlyph[currency] : "";
  return `${glyph}${Math.round(value).toLocaleString("en-US")}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Failed to load EODHD status";
}

function countText(value: number | null | undefined): string {
  return value == null ? "--" : value.toLocaleString("en-US");
}

function listText(items: string[]): string {
  return items.length === 0 ? "--" : items.join(", ");
}

function pathLabel(path: string): string {
  const parts = path.split("/").filter(Boolean);
  if (parts.length <= 3) {
    return path;
  }
  return `.../${parts.slice(-3).join("/")}`;
}

function EODHDUserInfoPanel({
  info,
  loading,
  error,
}: {
  info: EODHDUserInfo | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <div className="futumoo-user-panel is-muted">
        <span className="futumoo-user-kicker">EODHD</span>
        <strong>Loading status</strong>
        <span>Checking package and cache</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="futumoo-user-panel is-error">
        <span className="futumoo-user-kicker">EODHD</span>
        <strong>Unavailable</strong>
        <span>{error}</span>
      </div>
    );
  }
  if (!info) {
    return (
      <div className="futumoo-user-panel is-muted">
        <span className="futumoo-user-kicker">EODHD</span>
        <strong>No status</strong>
        <span>Market service returned no data</span>
      </div>
    );
  }

  const plan = info.all_in_one_assumed ? "All-in-one" : "Configured";

  return (
    <div className="futumoo-user-panel">
      <div className="futumoo-user-main">
        <div className="futumoo-user-identity">
          <span className="futumoo-user-kicker">EODHD</span>
          <strong>{info.api_token_label}</strong>
          <span>Package {info.package_version}</span>
        </div>
      </div>
      <div className="futumoo-user-grid">
        <span>PLAN</span>
        <strong>{plan}</strong>
        <span>EXCH</span>
        <strong>{listText(info.configured_exchanges)}</strong>
        <span>SYMS</span>
        <strong>{countText(info.code_names_count)}</strong>
        <span>DAY</span>
        <strong>{info.last_daily_date ?? "--"}</strong>
        <span>5MIN</span>
        <strong>{info.last_five_minute_date ?? "--"}</strong>
        <span>ROOT</span>
        <strong title={info.market_data_root}>{pathLabel(info.market_data_root)}</strong>
      </div>
      <div className="futumoo-user-status">
        <span>CSV cache</span>
        <span>EODHD flavor</span>
      </div>
    </div>
  );
}

export function EODHDApp() {
  const api = useMemo(() => createArenaApi("/eodhd"), []);
  const arena = useArena(api);
  const reports = useDailyReports(api, arena.selectedAgentId);
  const { topSeries, loadingTop } = useLeaderboardCurves(api, arena.rankings);
  const [leaderboardOpen, toggleLeaderboard] = usePersistentToggle(
    "quant-arena-eodhd-leaderboard",
    true,
  );
  const [manualResetOpen, setManualResetOpen] = useState(false);
  const [userInfo, setUserInfo] = useState<EODHDUserInfo | null>(null);
  const [loadingUserInfo, setLoadingUserInfo] = useState(true);
  const [userInfoError, setUserInfoError] = useState<string | null>(null);
  const stamp = todayStamp();

  useEffect(() => {
    let cancelled = false;
    setLoadingUserInfo(true);
    setUserInfoError(null);
    api
      .getEODHDUserInfo()
      .then((data) => {
        if (!cancelled) {
          setUserInfo(data);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setUserInfo(null);
          setUserInfoError(errorMessage(err));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingUserInfo(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const selectedRanking = arena.snapshot
    ? arena.rankings.find((entry) => entry.agent_id === arena.snapshot?.agent.agent_id) ?? null
    : null;

  function deleteSelectedAgent() {
    const agentId = arena.snapshot?.agent.agent_id;
    if (!agentId) {
      return;
    }
    if (!window.confirm(`Delete eodhd agent ${agentId}?`)) {
      return;
    }
    void arena.deleteAgent(agentId);
  }

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
            EODHD · <em>Data</em>
            <span className="glyph">数</span>
          </h1>
          <div className="masthead-han">全 市 场 日 线 通 鉴</div>
        </div>
        <div className="masthead-meta">
          <span>ALL-IN-ONE MARKET DATA · PAPER EXECUTION</span>
          <span>CSV PERSISTENCE · EODHD FLAVOR</span>
          <span>UTC INTRADAY · BULK EOD</span>
          <span>
            {arena.loadingAgents || arena.loadingRankings ? (
              <>
                <span className="dot dot-soft" />
                UPDATING
              </>
            ) : (
              <>
                <span className="dot dot-rise" />
                {arena.rankings.length} AGENTS RANKED
              </>
            )}
          </span>
          <EODHDUserInfoPanel info={userInfo} loading={loadingUserInfo} error={userInfoError} />
        </div>
      </header>
      <div className="rule-thick" />

      <Leaderboard
        topSeries={topSeries}
        loadingTop={loadingTop}
        rankingsCount={arena.rankings.length}
        open={leaderboardOpen}
        onToggle={toggleLeaderboard}
        formatAmount={formatAmount}
      />

      <main className="board-grid">
        <aside className="board-rail">
          <Roster
            rankings={arena.rankings}
            agentById={arena.agentById}
            selectedAgentId={arena.selectedAgentId}
            loadingRankings={arena.loadingRankings}
            onSelect={arena.selectAgent}
            formatAmount={formatAmount}
          />
          <EnlistForm
            placeholders={{ agentId: "eodhd-01", displayName: "Global Ledger" }}
            createdToken={arena.createdToken}
            createdAgentId={arena.createdAgentId}
            onCreate={arena.createAgent}
            currencyOptions={[
              { value: "USD", label: "USD · US Dollar" },
              { value: "HKD", label: "HKD · Hong Kong Dollar" },
              { value: "CNY", label: "CNY · Chinese Yuan" },
            ]}
          />
        </aside>

        <SnapshotPanel
          key={arena.selectedAgentId}
          snapshot={arena.snapshot}
          loadingSnapshot={arena.loadingSnapshot}
          selectedRanking={selectedRanking}
          specialEvents={arena.specialEvents}
          loadingSpecialEvents={arena.loadingSpecialEvents}
          destinations={arena.destinations}
          agentTargets={arena.agentTargets}
          savingTargets={arena.savingTargets}
          onToggleTarget={arena.toggleAgentTarget}
          onManualReset={() => setManualResetOpen(true)}
          onDelete={deleteSelectedAgent}
          symbolHeader="Symbol"
          formatAmount={formatAmount}
          formatYAxisLabel={formatYAxisLabel}
          formatDateTime={formatDateTime}
          formatTime={formatTime}
        />
      </main>

      {arena.snapshot && (
        <ReportsSection
          key={arena.snapshot.agent.agent_id}
          agentDisplayName={arena.snapshot.agent.display_name}
          reportsList={reports.reportsList}
          loadingList={reports.loadingList}
          selectedReport={reports.selectedReport}
          selectedReportDate={reports.selectedReportDate}
          loadingDetail={reports.loadingDetail}
          onSelectDate={(tradeDate) =>
            void reports.loadReportDetail(arena.snapshot!.agent.agent_id, tradeDate)
          }
          formatDateTime={formatDateTime}
        />
      )}

      <footer className="board-foot">
        <span>EODHD all-in-one data · Paper execution</span>
        <span>量化竞技场 · EODHD Edition</span>
      </footer>

      {manualResetOpen && arena.snapshot && (
        <ManualResetModal
          agentDisplayName={arena.snapshot.agent.display_name}
          onClose={() => setManualResetOpen(false)}
          onConfirm={(form) => arena.manualClear(arena.snapshot!.agent.agent_id, form)}
        />
      )}
    </div>
  );
}
