import { renderToStaticMarkup } from "react-dom/server";
import { createElement as h } from "react";
import { CurveChart, type CurveSeries } from "../src/CurveChart";

const series: CurveSeries[] = [
  {
    id: "agent-a",
    label: "Agent A",
    currency: null,
    color: "hsl(210 58% 42%)",
    points: [
      { date: "2026-06-01", equity: 60000, totalReturnPct: 0, dailyReturnPct: null },
      { date: "2026-06-02", equity: 62000, totalReturnPct: 3.33, dailyReturnPct: 3.33 },
      { date: "2026-06-03", equity: 61000, totalReturnPct: 1.66, dailyReturnPct: -1.6 },
      { date: "2026-06-04", equity: 65000, totalReturnPct: 8.33, dailyReturnPct: 6.55 },
      { date: "2026-06-05", equity: 64000, totalReturnPct: 6.66, dailyReturnPct: -1.53 },
    ],
  },
];

const fmt = (v: number | null | undefined) => String(Math.round(Number(v)));

const markup = renderToStaticMarkup(
  h(CurveChart, {
    series,
    mode: "equity",
    height: 240,
    formatAmount: fmt,
    formatYAxisLabel: fmt,
  }),
);

console.log(markup);
