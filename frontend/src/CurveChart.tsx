import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { scaleLinear, scalePoint } from "@visx/scale";
import { LinePath, AreaClosed, Line } from "@visx/shape";
import { Group } from "@visx/group";
import { AxisLeft, AxisBottom } from "@visx/axis";
import { useTooltip, TooltipWithBounds } from "@visx/tooltip";
import { localPoint } from "@visx/event";
import type { ArenaCurrency } from "./lib/types";
import { formatDateShort, pctClass, signedPct } from "./lib/format";

export type { ArenaCurrency };

/** One vertex of an agent's curve. Returns are precomputed by the caller. */
export type CurvePoint = {
  /** ISO trade date, e.g. "2026-06-21". */
  date: string;
  /** Absolute portfolio value in the series currency. */
  equity: number;
  /** Cumulative return vs. initial_cash, in percent (e.g. 12.4 → +12.40%). */
  totalReturnPct: number;
  /** That-day return vs. the previous point; null for an agent's first ever day. */
  dailyReturnPct: number | null;
};

export type CurveSeries = {
  /** Stable agent id — also drives the deterministic color. */
  id: string;
  label: string;
  currency: ArenaCurrency;
  /** Deterministic stroke color (see {@link agentColor}). */
  color: string;
  points: CurvePoint[];
};

/** Which value the Y-axis plots. "equity" is single-currency (per-agent);
 * "return" is comparable across currencies (the multi-agent leaderboard). */
export type CurveMode = "equity" | "return";

/**
 * Deterministic per-agent color, computed entirely frontend-side so the backend
 * never has to store one. A 32-bit string hash feeds three independent channels
 * (hue / saturation / lightness) so two different ids separate on more than hue
 * alone; S and L are clamped to a band that reads on the cream `--paper`.
 * Same id → same color everywhere (chart, legend), regardless of who else is shown.
 */
export function agentColor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) {
    h = (h * 31 + id.charCodeAt(i)) | 0;
  }
  const hue = Math.abs(h) % 360;
  const sat = 52 + (Math.abs(h >> 3) % 14); // 52–65%
  const light = 37 + (Math.abs(h >> 7) % 11); // 37–47%
  // Comma form: legacy SVG presentation-attribute color parsers reject the
  // CSS Color 4 space-separated syntax, which would silently drop the stroke.
  return `hsl(${hue}, ${sat}%, ${light}%)`;
}

type Vertex = { sx: number; sy: number; series: CurveSeries; point: CurvePoint };

export type CurveChartProps = {
  series: CurveSeries[];
  mode: CurveMode;
  /** Fixed pixel height; width is measured from the container. */
  height?: number;
  /** Currency-aware amount formatter (used in the tooltip and equity ticks). */
  formatAmount: (value: number | null | undefined, currency: ArenaCurrency) => string;
  /** Compact Y-axis label formatter for equity mode. */
  formatYAxisLabel?: (value: number, currency: ArenaCurrency) => string;
  /** Render a color/return legend below the chart (for the multi-agent view). */
  showLegend?: boolean;
};

/**
 * Reusable equity / return curve chart. One series → per-agent equity curve;
 * many series → leaderboard return overlay. The X-domain is the union of all
 * series' trade dates (evenly spaced — trading days only, no calendar gaps),
 * so the caller controls the window purely by which points it passes in.
 */
/**
 * Measure the container's content width via ResizeObserver. We deliberately
 * avoid visx's `ParentSize`: it fills its parent with an absolutely-positioned,
 * `overflow:hidden` box, which collapses to 0 height (clipping the chart away)
 * unless the parent has an explicit height. Measuring width ourselves keeps the
 * SVG in normal flow, so the container grows to fit the chart + legend.
 */
function useContainerWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      setWidth(entries[0]?.contentRect.width ?? 0);
    });
    observer.observe(el);
    setWidth(el.clientWidth);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

export function CurveChart(props: CurveChartProps) {
  const [ref, width] = useContainerWidth();
  return (
    <div className="curve-chart" ref={ref}>
      {/* width 0 on the first paint → fall back to 800, then self-correct. */}
      <CurveChartInner {...props} width={width || 800} />
    </div>
  );
}

function CurveChartInner({
  series,
  mode,
  width,
  height = 240,
  formatAmount,
  formatYAxisLabel,
  showLegend,
}: CurveChartProps & { width: number }) {
  const {
    showTooltip,
    hideTooltip,
    tooltipData,
    tooltipLeft,
    tooltipTop,
    tooltipOpen,
  } = useTooltip<Vertex>();

  const value = (p: CurvePoint) => (mode === "equity" ? p.equity : p.totalReturnPct);

  // Union of all trade dates, ascending → evenly spaced trading-day slots.
  const dates = useMemo(() => {
    const set = new Set<string>();
    for (const s of series) for (const p of s.points) set.add(p.date);
    return Array.from(set).sort();
  }, [series]);

  const margin = { top: 16, right: 18, bottom: 26, left: mode === "equity" ? 62 : 48 };
  const innerW = Math.max(0, width - margin.left - margin.right);
  const innerH = Math.max(0, height - margin.top - margin.bottom);

  const xScale = useMemo(
    () => scalePoint<string>({ domain: dates, range: [0, innerW], padding: 0.5 }),
    [dates, innerW],
  );

  const yScale = useMemo(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const s of series)
      for (const p of s.points) {
        const v = value(p);
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    if (mode === "return") {
      lo = Math.min(lo, 0);
      hi = Math.max(hi, 0);
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
      lo = 0;
      hi = 1;
    }
    const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.08 || 1;
    return scaleLinear<number>({
      domain: [lo - pad, hi + pad],
      range: [innerH, 0],
      nice: true,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, innerH, mode]);

  const vertices = useMemo(() => {
    const out: Vertex[] = [];
    for (const s of series) {
      for (const p of s.points) {
        const sx = xScale(p.date);
        if (sx == null) continue;
        out.push({ sx: sx + margin.left, sy: yScale(value(p)) + margin.top, series: s, point: p });
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, xScale, yScale, margin.left, margin.top]);

  if (dates.length === 0 || vertices.length === 0) {
    return <div className="curve-placeholder">No equity history yet</div>;
  }

  const handleMove = (event: MouseEvent<SVGRectElement>) => {
    const pt = localPoint(event);
    if (!pt) return;
    let best: Vertex | null = null;
    let bestD = Infinity;
    for (const v of vertices) {
      const dx = v.sx - pt.x;
      const dy = v.sy - pt.y;
      const d = dx * dx + dy * dy;
      if (d < bestD) {
        bestD = d;
        best = v;
      }
    }
    if (best) {
      showTooltip({ tooltipData: best, tooltipLeft: best.sx, tooltipTop: best.sy });
    }
  };

  const axisCurrency = series[0]?.currency ?? null;
  const tickEvery = Math.max(1, Math.ceil(dates.length / 6));
  const xTicks = dates.filter((_, i) => i % tickEvery === 0);
  const sorted = (s: CurveSeries) => [...s.points].sort((a, b) => a.date.localeCompare(b.date));

  return (
    <>
      <svg width={width} height={height} className="curve-svg">
        <Group left={margin.left} top={margin.top}>
          {mode === "return" && (
            <Line
              className="curve-zero"
              from={{ x: 0, y: yScale(0) }}
              to={{ x: innerW, y: yScale(0) }}
            />
          )}
          {yScale.ticks(4).map((t) => (
            <Line
              key={`g${t}`}
              className="curve-grid"
              from={{ x: 0, y: yScale(t) }}
              to={{ x: innerW, y: yScale(t) }}
            />
          ))}
          {/* Single-series equity view gets a soft fill under the curve. */}
          {mode === "equity" && series.length === 1 && (
            <AreaClosed<CurvePoint>
              data={sorted(series[0])}
              x={(p) => xScale(p.date) ?? 0}
              y={(p) => yScale(value(p))}
              yScale={yScale}
              className="curve-area"
              style={{ fill: series[0].color }}
            />
          )}
          {series.map((s) => (
            <LinePath<CurvePoint>
              key={s.id}
              className="curve-line"
              data={sorted(s)}
              x={(p) => xScale(p.date) ?? 0}
              y={(p) => yScale(value(p))}
              stroke={s.color}
              strokeWidth={series.length > 1 ? 1.6 : 1.9}
            />
          ))}
          <AxisLeft
            scale={yScale}
            numTicks={4}
            hideAxisLine
            hideTicks
            tickFormat={(v) =>
              mode === "equity"
                ? (formatYAxisLabel
                    ? formatYAxisLabel(Number(v), axisCurrency)
                    : formatAmount(Number(v), axisCurrency))
                : signedPct(Number(v), 0)
            }
            tickClassName="curve-tick"
            tickLabelProps={() => ({
              className: "curve-tick-label",
              dx: "-0.25em",
              dy: "0.25em",
              textAnchor: "end" as const,
            })}
          />
          <AxisBottom
            top={innerH}
            scale={xScale}
            tickValues={xTicks}
            hideAxisLine
            hideTicks
            tickFormat={(d) => formatDateShort(String(d))}
            tickClassName="curve-tick"
            tickLabelProps={() => ({
              className: "curve-tick-label",
              dy: "0.6em",
              textAnchor: "middle" as const,
            })}
          />
        </Group>

        {tooltipOpen && tooltipData && (
          <>
            <Line
              className="curve-crosshair"
              from={{ x: tooltipData.sx, y: margin.top }}
              to={{ x: tooltipData.sx, y: height - margin.bottom }}
            />
            <circle className="curve-marker-halo" cx={tooltipData.sx} cy={tooltipData.sy} r={6} />
            <circle
              className="curve-marker"
              cx={tooltipData.sx}
              cy={tooltipData.sy}
              r={3.5}
              style={{ fill: tooltipData.series.color }}
            />
          </>
        )}

        <rect
          x={margin.left}
          y={margin.top}
          width={innerW}
          height={innerH}
          fill="transparent"
          onMouseMove={handleMove}
          onMouseLeave={() => hideTooltip()}
        />
      </svg>

      {tooltipOpen && tooltipData && (
        <TooltipWithBounds
          key={`${tooltipData.series.id}-${tooltipData.point.date}`}
          left={tooltipLeft}
          top={tooltipTop}
          className="curve-tooltip"
          unstyled
        >
          <div className="ct-name" style={{ color: tooltipData.series.color }}>
            {tooltipData.series.label}
          </div>
          <div className="ct-row">
            <span>Date</span>
            <b>{formatDateShort(tooltipData.point.date)}</b>
          </div>
          <div className="ct-row">
            <span>Equity</span>
            <b>{formatAmount(tooltipData.point.equity, tooltipData.series.currency)}</b>
          </div>
          <div className="ct-row">
            <span>Return</span>
            <b className={pctClass(tooltipData.point.totalReturnPct)}>
              {signedPct(tooltipData.point.totalReturnPct)}
            </b>
          </div>
          <div className="ct-row">
            <span>That day</span>
            <b className={pctClass(tooltipData.point.dailyReturnPct)}>
              {tooltipData.point.dailyReturnPct == null
                ? "—"
                : signedPct(tooltipData.point.dailyReturnPct)}
            </b>
          </div>
        </TooltipWithBounds>
      )}

      {showLegend && (
        <div className="curve-legend">
          {series.map((s) => {
            const last = s.points[s.points.length - 1];
            const ret = last?.totalReturnPct ?? 0;
            return (
              <span key={s.id} className="cl-item">
                <i className="cl-swatch" style={{ background: s.color }} />
                <span className="cl-label">{s.label}</span>
                <b className={`cl-return ${pctClass(ret)}`}>{signedPct(ret)}</b>
              </span>
            );
          })}
        </div>
      )}
    </>
  );
}

/**
 * Build a {@link CurveSeries} from a raw oldest-first equity history. Returns are
 * computed over the FULL history; slice `points` afterward (e.g. last 30) so the
 * first windowed point still carries a correct that-day return.
 */
export function buildCurveSeries(
  id: string,
  label: string,
  currency: ArenaCurrency,
  initialCash: number,
  equity: { trade_date: string; total_equity: number }[],
): CurveSeries {
  const base = initialCash || equity[0]?.total_equity || 1;
  const points: CurvePoint[] = equity.map((p, i) => {
    const prev = i > 0 ? equity[i - 1].total_equity : null;
    return {
      date: p.trade_date,
      equity: p.total_equity,
      totalReturnPct: ((p.total_equity - base) / base) * 100,
      dailyReturnPct:
        prev != null && prev !== 0 ? ((p.total_equity - prev) / prev) * 100 : null,
    };
  });
  return { id, label, currency, color: agentColor(id), points };
}
