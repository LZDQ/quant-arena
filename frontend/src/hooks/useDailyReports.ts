import { useCallback, useEffect, useState } from "react";
import type { ArenaApi } from "../lib/api";
import type { DailyReport, DailyReportPage } from "../lib/types";
import { formatDateKey } from "../lib/format";
import { useToast } from "../components/ui";

const REPORTS_PAGE_SIZE = 100;

/**
 * Daily-report list + selected-report detail for one agent. Refreshes whenever
 * the selected agent changes and auto-opens today's report when present, so the
 * panel lands on "today" instead of an empty prompt.
 */
export function useDailyReports(api: ArenaApi, selectedAgentId: string) {
  const { error: toastError } = useToast();
  const [reportsList, setReportsList] = useState<DailyReportPage | null>(null);
  const [loadingList, setLoadingList] = useState(false);
  const [selectedReportDate, setSelectedReportDate] = useState<string>("");
  const [selectedReport, setSelectedReport] = useState<DailyReport | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const loadReportDetail = useCallback(
    async (agentId: string, tradeDate: string) => {
      setSelectedReportDate(tradeDate);
      setLoadingDetail(true);
      try {
        const data = await api.getDailyReport(agentId, tradeDate);
        setSelectedReport(data);
      } catch (err) {
        toastError((err as Error).message);
        setSelectedReport(null);
      } finally {
        setLoadingDetail(false);
      }
    },
    [api, toastError],
  );

  useEffect(() => {
    if (!selectedAgentId) {
      setReportsList(null);
      setSelectedReport(null);
      setSelectedReportDate("");
      return;
    }
    let cancelled = false;
    setSelectedReport(null);
    setSelectedReportDate("");
    setLoadingList(true);
    void (async () => {
      try {
        const data = await api.listDailyReports(selectedAgentId, 1, REPORTS_PAGE_SIZE);
        if (cancelled) return;
        setReportsList(data);
        const now = new Date();
        const todayKey = formatDateKey(now.getFullYear(), now.getMonth(), now.getDate());
        if (data.items.some((item) => item.trade_date === todayKey)) {
          void loadReportDetail(selectedAgentId, todayKey);
        }
      } catch (err) {
        if (cancelled) return;
        toastError((err as Error).message);
        setReportsList(null);
      } finally {
        if (!cancelled) setLoadingList(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [api, selectedAgentId, loadReportDetail, toastError]);

  return {
    reportsList,
    loadingList,
    selectedReport,
    selectedReportDate,
    loadingDetail,
    loadReportDetail,
  };
}
