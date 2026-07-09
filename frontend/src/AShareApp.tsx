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
import type { ArenaCurrency } from "./lib/types";

const BASE_URL = urlPrefix();

const yuanFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 2,
});

function formatAmount(value: number | null | undefined, _currency: ArenaCurrency): string {
  if (value == null) {
    return "--";
  }
  return `¥${yuanFormatter.format(value)}`;
}

const datetimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

const timeFormatter = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return datetimeFormatter.format(new Date(value));
}

function formatTime(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return timeFormatter.format(new Date(value));
}

function formatYAxisLabel(value: number, _currency: ArenaCurrency): string {
  return `¥${Math.round(value).toLocaleString("en-US")}`;
}

export function AShareApp() {
  const api = useMemo(() => createArenaApi(""), []);
  const arena = useArena(api);
  const reports = useDailyReports(api, arena.selectedAgentId);
  const { topSeries, loadingTop } = useLeaderboardCurves(api, arena.rankings);
  const [leaderboardOpen, toggleLeaderboard] = usePersistentToggle(
    "quant-arena-ashare-leaderboard",
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
    if (!window.confirm(`Delete agent ${agentId}?`)) {
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
            placeholders={{ agentId: "trader-01", displayName: "The Iron Pen" }}
            createdToken={arena.createdToken}
            createdAgentId={arena.createdAgentId}
            onCreate={arena.createAgent}
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
          symbolHeader="Code"
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
        <span>Composed nightly · Bureau of Simulated Equities</span>
        <span>量化竞技场 · A-Share Edition</span>
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
