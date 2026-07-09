import { useMemo, useState } from "react";

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
import type { ArenaCurrency, Currency } from "./lib/types";

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
};

const currencyGlyph: Record<Currency, string> = {
  HKD: "HK$",
  USD: "$",
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

export function FutumooApp() {
  const api = useMemo(() => createArenaApi("/futumoo"), []);
  const arena = useArena(api);
  const reports = useDailyReports(api, arena.selectedAgentId);
  const { topSeries, loadingTop } = useLeaderboardCurves(api, arena.rankings);
  const [leaderboardOpen, toggleLeaderboard] = usePersistentToggle(
    "quant-arena-futumoo-leaderboard",
    true,
  );
  const [manualResetOpen, setManualResetOpen] = useState(false);
  const stamp = todayStamp();

  const selectedRanking = arena.snapshot
    ? arena.rankings.find((entry) => entry.agent_id === arena.snapshot?.agent.agent_id) ?? null
    : null;

  function deleteSelectedAgent() {
    const agentId = arena.snapshot?.agent.agent_id;
    if (!agentId) {
      return;
    }
    if (!window.confirm(`Delete futumoo agent ${agentId}?`)) {
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
            Futu · <em>Moo</em>
            <span className="glyph">富</span>
          </h1>
          <div className="masthead-han">富 途 离 线 通 鉴</div>
        </div>
        <div className="masthead-meta">
          <span>OFFLINE PAPER · BUREAU OF SIMULATED EQUITIES</span>
          <span>HK · US VIA FUTU OPEND</span>
          <span>ONE CURRENCY PER AGENT · NO T+1</span>
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
            placeholders={{ agentId: "moo-01", displayName: "The Mooing Bull" }}
            createdToken={arena.createdToken}
            createdAgentId={arena.createdAgentId}
            onCreate={arena.createAgent}
            currencyOptions={[
              { value: "HKD", label: "HKD · Hong Kong Dollar" },
              { value: "USD", label: "USD · US Dollar" },
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
        <span>Composed offline · Bureau of Simulated Equities</span>
        <span>量化竞技场 · Futu Moo Edition</span>
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
