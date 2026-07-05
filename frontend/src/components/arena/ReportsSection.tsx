import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { DailyReport, DailyReportPage, DailyReportSummary } from "../../lib/types";
import { formatDateKey, pad2 } from "../../lib/format";

const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

type ReportsSectionProps = {
  agentDisplayName: string;
  reportsList: DailyReportPage | null;
  loadingList: boolean;
  selectedReport: DailyReport | null;
  selectedReportDate: string;
  loadingDetail: boolean;
  onSelectDate: (tradeDate: string) => void;
  formatDateTime: (value: string | null | undefined) => string;
};

/**
 * Daily-report calendar + reader. Mount with `key={agentId}` so switching
 * agents resets the visible month to "today". Note: the calendar can only
 * reflect the reports present in `reportsList` (the newest page), so months
 * older than that page render as empty.
 */
export function ReportsSection({
  agentDisplayName,
  reportsList,
  loadingList,
  selectedReport,
  selectedReportDate,
  loadingDetail,
  onSelectDate,
  formatDateTime,
}: ReportsSectionProps) {
  const [calendarMonth, setCalendarMonth] = useState<{ year: number; month: number }>(() => {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() };
  });

  const reportsTotal = reportsList?.total ?? 0;
  const reportsItems = reportsList?.items ?? [];

  const reportsByDate = useMemo(() => {
    const map = new Map<string, DailyReportSummary>();
    for (const item of reportsItems) {
      map.set(item.trade_date, item);
    }
    return map;
  }, [reportsItems]);

  const calendarCells = useMemo(() => {
    const { year, month } = calendarMonth;
    const firstWeekday = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const cells: ({ key: string; day: number } | null)[] = [];
    for (let i = 0; i < firstWeekday; i += 1) {
      cells.push(null);
    }
    for (let day = 1; day <= daysInMonth; day += 1) {
      cells.push({ key: formatDateKey(year, month, day), day });
    }
    while (cells.length % 7 !== 0) {
      cells.push(null);
    }
    return cells;
  }, [calendarMonth]);

  const todayKey = useMemo(() => {
    const now = new Date();
    return formatDateKey(now.getFullYear(), now.getMonth(), now.getDate());
  }, []);

  const calendarMonthLabel = `${calendarMonth.year}-${pad2(calendarMonth.month + 1)}`;

  function shiftCalendarMonth(delta: number) {
    setCalendarMonth(({ year, month }) => {
      const next = new Date(year, month + delta, 1);
      return { year: next.getFullYear(), month: next.getMonth() };
    });
  }

  return (
    <section className="reports-section">
      <div className="section-head reports-section-head">
        <h3>Daily Reports · {agentDisplayName}</h3>
        <span className="meta">{reportsTotal} entries</span>
      </div>
      <div className="reports-layout">
        <aside className="reports-calendar">
          <div className="calendar-head">
            <button
              type="button"
              className="calendar-nav"
              onClick={() => shiftCalendarMonth(-1)}
              aria-label="Previous month"
            >
              ←
            </button>
            <span className="calendar-title">{calendarMonthLabel}</span>
            <button
              type="button"
              className="calendar-nav"
              onClick={() => shiftCalendarMonth(1)}
              aria-label="Next month"
            >
              →
            </button>
          </div>
          <div className="calendar-grid">
            {WEEKDAY_LABELS.map((label) => (
              <span key={label} className="calendar-dow">
                {label}
              </span>
            ))}
            {calendarCells.map((cell, idx) => {
              if (!cell) {
                return <span key={`blank-${idx}`} className="calendar-cell is-blank" />;
              }
              const hasReport = reportsByDate.has(cell.key);
              const isActive = selectedReportDate === cell.key;
              const isCellToday = cell.key === todayKey;
              const classes = [
                "calendar-cell",
                hasReport ? "has-report" : "no-report",
                isActive ? "is-active" : "",
                isCellToday ? "is-today" : "",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <button
                  key={cell.key}
                  type="button"
                  className={classes}
                  disabled={!hasReport || loadingDetail}
                  onClick={() => onSelectDate(cell.key)}
                >
                  {cell.day}
                </button>
              );
            })}
          </div>
          <div className="calendar-meta">
            {loadingList
              ? "Loading…"
              : reportsTotal === 0
                ? "No reports yet"
                : "· filled days have reports ·"}
          </div>
        </aside>
        <div className="reports-detail">
          {loadingDetail ? (
            <div className="reports-empty">Loading report…</div>
          ) : selectedReport ? (
            <article className="reports-article">
              <header className="reports-detail-head">
                <span className="reports-detail-date">{selectedReport.trade_date}</span>
                <span className="reports-detail-meta">
                  Updated {formatDateTime(selectedReport.updated_at)}
                </span>
              </header>
              <div className="reports-detail-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedReport.content}</ReactMarkdown>
              </div>
            </article>
          ) : (
            <div className="reports-empty">
              — pick a date on the calendar to read the report —
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
