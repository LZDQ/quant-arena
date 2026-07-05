import type { AgentResponse, Currency, RankingEntry } from "../../lib/types";
import { percentClass, signedPct } from "../../lib/format";

type RosterProps = {
  rankings: RankingEntry[];
  agentById: Map<string, AgentResponse>;
  selectedAgentId: string;
  loadingRankings: boolean;
  onSelect: (agentId: string) => void;
  formatAmount: (value: number | null | undefined, currency: Currency) => string;
};

/** Ranked agent list down the left rail; click to open an agent's book. */
export function Roster({
  rankings,
  agentById,
  selectedAgentId,
  loadingRankings,
  onSelect,
  formatAmount,
}: RosterProps) {
  return (
    <>
      <div className="section-head">
        <h3>Roster</h3>
        <span className="meta">Ranked · Return %</span>
      </div>
      <div className="roster">
        {rankings.map((entry, index) => {
          const agent = agentById.get(entry.agent_id);
          const isActive = selectedAgentId === entry.agent_id;
          return (
            <button
              key={entry.agent_id}
              type="button"
              className={`roster-row ${isActive ? "is-active" : ""}`}
              data-currency={entry.currency}
              onClick={() => onSelect(entry.agent_id)}
            >
              <span className="roster-rank">{String(index + 1).padStart(2, "0")}</span>
              <span>
                <div className="roster-name">{entry.display_name}</div>
                <div className="roster-id">{entry.agent_id}</div>
                <span
                  className="roster-meta-row"
                  style={{ marginTop: 8, display: "inline-flex", gap: 6 }}
                >
                  <span className={`roster-pill currency currency-${entry.currency}`}>
                    {entry.currency}
                  </span>
                  {agent && agent.ib_mode && (
                    <span className={`roster-pill ib-${agent.ib_mode}`}>
                      {agent.ib_mode.toUpperCase()}
                    </span>
                  )}
                  {agent && (
                    <span className={`roster-pill ${agent.enabled ? "live" : ""}`}>
                      {agent.enabled ? "LIVE" : "OFF"} · {agent.role.toUpperCase()}
                    </span>
                  )}
                </span>
              </span>
              <span className="roster-stats">
                <span className="roster-equity">
                  {formatAmount(entry.total_equity, entry.currency)}
                </span>
                <span className={`roster-return ${percentClass(entry.return_pct)}`}>
                  {signedPct(entry.return_pct)}
                </span>
              </span>
            </button>
          );
        })}
        {!loadingRankings && rankings.length === 0 && (
          <p className="empty-line">No rankings yet · enlist an agent below</p>
        )}
      </div>
    </>
  );
}
