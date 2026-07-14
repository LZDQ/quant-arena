import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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
import type {
  ArenaCurrency,
  Currency,
  FutumooSubscriptionStatus,
  FutumooUserInfo,
} from "./lib/types";

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

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function statusText(value: boolean): string {
  return value ? "ON" : "OFF";
}

function quotaText(value: number | null): string {
  return value == null ? "--" : value.toLocaleString("en-US");
}

function infoText(value: string | number | null): string {
  if (value == null || value === "") {
    return "N/A";
  }
  return typeof value === "number" ? value.toLocaleString("en-US") : value;
}

function FutumooInfoMetric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="futumoo-user-metric">
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function FutumooUserInfoPanel({
  info,
  loading,
  error,
}: {
  info: FutumooUserInfo | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <div className="futumoo-user-panel futumoo-user-info-panel is-muted">
        <span className="futumoo-user-kicker">Futu User</span>
        <strong>Connecting to OpenD</strong>
        <span>Loading login state</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="futumoo-user-panel futumoo-user-info-panel is-error">
        <span className="futumoo-user-kicker">Futu User</span>
        <strong>Unavailable</strong>
        <span>{error}</span>
      </div>
    );
  }
  if (!info) {
    return (
      <div className="futumoo-user-panel futumoo-user-info-panel is-muted">
        <span className="futumoo-user-kicker">Futu User</span>
        <strong>No OpenD user</strong>
        <span>Quote context returned no profile</span>
      </div>
    );
  }

  const displayName = info.nick_name ?? (info.user_id ? `User ${info.user_id}` : "OpenD User");
  const userId = info.user_id ?? info.login_user_id ?? "--";
  const programStatus = info.program_status_type ?? "UNKNOWN";
  const openDVersion = info.server_ver ? `OpenD ${info.server_ver}` : "OpenD";
  const disclaimer =
    info.is_need_agree_disclaimer == null
      ? "N/A"
      : info.is_need_agree_disclaimer
        ? "REQUIRED"
        : "CLEAR";

  return (
    <div className="futumoo-user-panel futumoo-user-info-panel">
      <div className="futumoo-user-main">
        {info.avatar_url && (
          <img className="futumoo-user-avatar" src={info.avatar_url} alt="" referrerPolicy="no-referrer" />
        )}
        <div className="futumoo-user-identity">
          <span className="futumoo-user-kicker">Futu User</span>
          <strong>{displayName}</strong>
          <span>ID {userId}</span>
        </div>
      </div>

      <div className="futumoo-user-section">
        <span className="futumoo-user-section-title">Account &amp; API</span>
        <div className="futumoo-user-metrics futumoo-user-account-metrics">
          <FutumooInfoMetric label="ATTRIBUTION" value={infoText(info.user_attr)} />
          <FutumooInfoMetric label="API LEVEL" value={infoText(info.api_level)} />
          <FutumooInfoMetric label="UPDATE" value={infoText(info.update_type)} />
          <FutumooInfoMetric label="DISCLAIMER" value={disclaimer} />
        </div>
      </div>

      <div className="futumoo-user-section">
        <span className="futumoo-user-section-title">Quote Rights</span>
        <div className="futumoo-user-metrics futumoo-user-rights-metrics">
          <FutumooInfoMetric label="HK" value={infoText(info.hk_qot_right)} />
          <FutumooInfoMetric label="HK OPTION" value={infoText(info.hk_option_qot_right)} />
          <FutumooInfoMetric label="HK FUTURE" value={infoText(info.hk_future_qot_right)} />
          <FutumooInfoMetric label="US" value={infoText(info.us_qot_right)} />
          <FutumooInfoMetric label="US OPTION" value={infoText(info.us_option_qot_right)} />
          <FutumooInfoMetric label="US FUTURE" value={infoText(info.us_future_qot_right)} />
          <FutumooInfoMetric label="CN" value={infoText(info.cn_qot_right)} />
          <FutumooInfoMetric label="SG FUTURE" value={infoText(info.sg_future_qot_right)} />
          <FutumooInfoMetric label="JP FUTURE" value={infoText(info.jp_future_qot_right)} />
          <FutumooInfoMetric label="CME" value={infoText(info.us_future_qot_right_cme)} />
          <FutumooInfoMetric label="CBOT" value={infoText(info.us_future_qot_right_cbot)} />
          <FutumooInfoMetric label="NYMEX" value={infoText(info.us_future_qot_right_nymex)} />
          <FutumooInfoMetric label="COMEX" value={infoText(info.us_future_qot_right_comex)} />
          <FutumooInfoMetric label="CBOE" value={infoText(info.us_future_qot_right_cboe)} />
        </div>
      </div>

      <div className="futumoo-user-section">
        <span className="futumoo-user-section-title">OpenD Runtime</span>
        <div className="futumoo-user-metrics futumoo-user-runtime-metrics">
          <FutumooInfoMetric label="QOT" value={statusText(info.qot_logined)} />
          <FutumooInfoMetric label="TRD" value={statusText(info.trd_logined)} />
          <FutumooInfoMetric label="LOGIN ID" value={infoText(info.login_user_id)} />
          <FutumooInfoMetric label="HK MKT" value={infoText(info.market_hk)} />
          <FutumooInfoMetric label="US MKT" value={infoText(info.market_us)} />
          <FutumooInfoMetric label="SH MKT" value={infoText(info.market_sh)} />
          <FutumooInfoMetric label="SZ MKT" value={infoText(info.market_sz)} />
          <FutumooInfoMetric label="SUB" value={quotaText(info.sub_quota)} />
          <FutumooInfoMetric label="KL" value={quotaText(info.history_kl_quota)} />
        </div>
      </div>
      <div className="futumoo-user-status">
        <span title={info.program_status_desc ?? undefined}>
          {programStatus}
          {info.program_status_desc ? ` · ${info.program_status_desc}` : ""}
        </span>
        <span>{openDVersion}</span>
      </div>
    </div>
  );
}

function FutumooSubscriptionPanel({
  status,
  loading,
  error,
  onRefresh,
}: {
  status: FutumooSubscriptionStatus | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  if (loading) {
    return (
      <div className="futumoo-user-panel is-muted">
        <span className="futumoo-user-kicker">Live Quote LRU</span>
        <strong>Loading subscriptions</strong>
        <button className="futumoo-subscription-refresh" type="button" disabled>
          Refreshing…
        </button>
      </div>
    );
  }
  if (error || !status) {
    return (
      <div className="futumoo-user-panel is-error">
        <span className="futumoo-user-kicker">Live Quote LRU</span>
        <strong>Unavailable</strong>
        <span>{error ?? "No subscription status"}</span>
        <button className="futumoo-subscription-refresh" type="button" onClick={onRefresh}>
          Refresh
        </button>
      </div>
    );
  }

  return (
    <div className="futumoo-user-panel futumoo-subscription-panel">
      <div className="futumoo-subscription-head">
        <span className="futumoo-user-kicker">Live Quote LRU</span>
        <div className="futumoo-subscription-actions">
          <strong>
            {status.subscribed_count} / {status.subscription_limit}
          </strong>
          <button className="futumoo-subscription-refresh" type="button" onClick={onRefresh}>
            Refresh
          </button>
        </div>
      </div>
      <div className="futumoo-subscription-list">
        {status.latest_accessed_symbols.length ? (
          status.latest_accessed_symbols.map((symbol) => (
            <div key={symbol.code}>
              <strong>{symbol.code}</strong>
              <span>{symbol.name ?? "Name pending"}</span>
            </div>
          ))
        ) : (
          <span className="futumoo-subscription-empty">No symbols subscribed</span>
        )}
      </div>
    </div>
  );
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
  const [userInfo, setUserInfo] = useState<FutumooUserInfo | null>(null);
  const [loadingUserInfo, setLoadingUserInfo] = useState(true);
  const [userInfoError, setUserInfoError] = useState<string | null>(null);
  const [subscriptionStatus, setSubscriptionStatus] =
    useState<FutumooSubscriptionStatus | null>(null);
  const [loadingSubscriptionStatus, setLoadingSubscriptionStatus] =
    useState(true);
  const [subscriptionStatusError, setSubscriptionStatusError] = useState<
    string | null
  >(null);
  const loadedSubscriptionStatus = useRef(false);
  const stamp = todayStamp();

  useEffect(() => {
    let cancelled = false;
    setLoadingUserInfo(true);
    setUserInfoError(null);
    api
      .getFutumooUserInfo()
      .then((data) => {
        if (!cancelled) {
          setUserInfo(data);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setUserInfo(null);
          setUserInfoError(errorMessage(err, "Failed to load Futu user info"));
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

  const refreshSubscriptionStatus = useCallback(async () => {
    setLoadingSubscriptionStatus(true);
    try {
      const data = await api.getFutumooSubscriptionStatus();
      setSubscriptionStatus(data);
      setSubscriptionStatusError(null);
    } catch (err: unknown) {
      setSubscriptionStatus(null);
      setSubscriptionStatusError(errorMessage(err, "Failed to load Futu subscriptions"));
    } finally {
      setLoadingSubscriptionStatus(false);
    }
  }, [api]);

  useEffect(() => {
    if (loadedSubscriptionStatus.current) {
      return;
    }
    loadedSubscriptionStatus.current = true;
    void refreshSubscriptionStatus();
  }, [refreshSubscriptionStatus]);

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
          <span>HK · US · CN VIA FUTU OPEND</span>
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
          <FutumooUserInfoPanel info={userInfo} loading={loadingUserInfo} error={userInfoError} />
          <FutumooSubscriptionPanel
            status={subscriptionStatus}
            loading={loadingSubscriptionStatus}
            error={subscriptionStatusError}
            onRefresh={() => void refreshSubscriptionStatus()}
          />
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
          savingAmnesia={arena.savingAmnesia}
          onToggleTarget={arena.toggleAgentTarget}
          onToggleAmnesia={arena.toggleAgentAmnesia}
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
