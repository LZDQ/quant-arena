import { CurveChart, type CurveSeries } from "../../CurveChart";
import type { ArenaCurrency } from "../../lib/types";

type LeaderboardProps = {
  topSeries: CurveSeries[];
  loadingTop: boolean;
  rankingsCount: number;
  open: boolean;
  onToggle: () => void;
  formatAmount: (value: number | null | undefined, currency: ArenaCurrency) => string;
};

/** Top-N return overlay above the board, foldable and persisted. */
export function Leaderboard({
  topSeries,
  loadingTop,
  rankingsCount,
  open,
  onToggle,
  formatAmount,
}: LeaderboardProps) {
  return (
    <section className="leaderboard">
      <div className="section-head">
        <h3>Leaderboard · Top {Math.min(topSeries.length || rankingsCount, 10)}</h3>
        <button type="button" className="fold-toggle" onClick={onToggle} aria-expanded={open}>
          <span className="meta">Last 30 Trading Days · Return %</span>
          <span className="fold-icon">{open ? "Fold ▲" : "Unfold ▼"}</span>
        </button>
      </div>
      {open &&
        (loadingTop && topSeries.length === 0 ? (
          <div className="curve-placeholder">Loading curves…</div>
        ) : topSeries.length > 0 ? (
          <CurveChart
            series={topSeries}
            mode="return"
            height={360}
            formatAmount={formatAmount}
            showLegend
          />
        ) : (
          <div className="curve-placeholder">No equity history yet</div>
        ))}
    </section>
  );
}
