import { useEffect, useMemo, useState } from "react";
import type { ArenaApi } from "../lib/api";
import type { AgentSnapshotResponse, RankingEntry } from "../lib/types";
import { buildCurveSeries, type CurveSeries } from "../CurveChart";

const TOP_N = 10;
const WINDOW_DAYS = 30;

/**
 * Equity/return curves for the top-N leaderboard. There is no bulk history
 * endpoint, so this fetches one snapshot per ranked agent and only refetches
 * when the *membership* of the top-N changes (rankings arrive pre-sorted by
 * return_pct desc). Curves are windowed to the last {@link WINDOW_DAYS} trading
 * days; returns stay correct because they were computed over the full history
 * before windowing.
 */
export function useLeaderboardCurves(api: ArenaApi, rankings: RankingEntry[]) {
  const [topSeries, setTopSeries] = useState<CurveSeries[]>([]);
  const [loadingTop, setLoadingTop] = useState(true);

  const topIds = useMemo(
    () => rankings.slice(0, TOP_N).map((entry) => entry.agent_id).join(","),
    [rankings],
  );

  useEffect(() => {
    if (!topIds) {
      setTopSeries([]);
      setLoadingTop(false);
      return;
    }
    let cancelled = false;
    setLoadingTop(true);
    void (async () => {
      const ids = topIds.split(",");
      const snaps = await Promise.all(
        ids.map((id) => api.getSnapshot(id).catch(() => null)),
      );
      if (cancelled) return;
      const full = snaps
        .filter((s): s is AgentSnapshotResponse => s != null && s.equity.length > 0)
        .map((s) =>
          buildCurveSeries(
            s.agent.agent_id,
            s.agent.display_name,
            s.agent.currency,
            s.agent.initial_cash,
            s.equity,
          ),
        );
      // Trading days = union of every agent's history; keep only the last window.
      const allDates = Array.from(
        new Set(full.flatMap((s) => s.points.map((p) => p.date))),
      ).sort();
      const windowDates = new Set(allDates.slice(-WINDOW_DAYS));
      const windowed = full
        .map((s) => ({ ...s, points: s.points.filter((p) => windowDates.has(p.date)) }))
        .filter((s) => s.points.length > 0);
      setTopSeries(windowed);
      setLoadingTop(false);
    })();
    return () => {
      cancelled = true;
    };
    // api is stable for the dashboard's lifetime; refetch only on membership change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topIds]);

  return { topSeries, loadingTop };
}
