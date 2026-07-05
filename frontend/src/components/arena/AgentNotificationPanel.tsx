import type { AgentNotificationTargets, NotificationDestinations } from "../../lib/types";

type NotifField = keyof AgentNotificationTargets;

type AgentNotificationPanelProps = {
  destinations: NotificationDestinations | null;
  agentTargets: AgentNotificationTargets | null;
  saving: boolean;
  onToggle: (field: NotifField, key: string) => void;
};

/** Per-agent notification routing: which configured destinations receive this
 * agent's order notifications and daily reports. */
export function AgentNotificationPanel({
  destinations,
  agentTargets,
  saving,
  onToggle,
}: AgentNotificationPanelProps) {
  if (!destinations) {
    return (
      <div className="agent-notif">
        <div className="agent-notif-label">Notifications</div>
        <div className="agent-notif-empty">Loading destinations…</div>
      </div>
    );
  }
  const napcatKeys = Object.keys(destinations.napcat_destinations);
  const napcatActive = new Set(agentTargets?.napcat ?? []);
  const dailyReportActive = new Set(agentTargets?.daily_report ?? []);
  const hasAny = napcatKeys.length > 0;

  const renderCards = (keys: string[], active: Set<string>, field: NotifField) => (
    <div className="agent-notif-cards">
      {keys.map((key) => {
        const isActive = active.has(key);
        return (
          <button
            key={key}
            type="button"
            className={`agent-notif-card ${isActive ? "is-active" : ""}`}
            onClick={() => onToggle(field, key)}
            disabled={saving}
            aria-pressed={isActive}
            title={isActive ? "Click to disable" : "Click to enable"}
          >
            <span className="agent-notif-card-key">{key}</span>
          </button>
        );
      })}
    </div>
  );

  const napcatStateLabel = (
    <span className={`agent-notif-channel-state ${destinations.napcat_enabled ? "on" : "off"}`}>
      {destinations.napcat_enabled ? "ON" : "OFF"}
    </span>
  );

  return (
    <div className="agent-notif">
      <div className="agent-notif-head">
        <span className="agent-notif-label">Notifications</span>
        <span className="agent-notif-meta">
          {saving ? "saving…" : "click a card to toggle"}
        </span>
      </div>
      {!hasAny ? (
        <div className="agent-notif-empty">
          No destinations configured. Add some on the markets page.
        </div>
      ) : (
        <div className="agent-notif-split">
          <div className="agent-notif-col">
            <span className="agent-notif-col-title">Order notifications</span>
            {napcatKeys.length > 0 && (
              <div className="agent-notif-channel">
                <span className="agent-notif-channel-label">NapCat {napcatStateLabel}</span>
                {renderCards(napcatKeys, napcatActive, "napcat")}
              </div>
            )}
          </div>
          <div className="agent-notif-col">
            <span className="agent-notif-col-title">Daily report</span>
            {napcatKeys.length > 0 ? (
              <div className="agent-notif-channel">
                <span className="agent-notif-channel-label">NapCat {napcatStateLabel}</span>
                {renderCards(napcatKeys, dailyReportActive, "daily_report")}
              </div>
            ) : (
              <div className="agent-notif-empty">No NapCat destinations.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
