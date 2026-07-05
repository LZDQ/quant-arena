import { useMemo, useState } from "react";
import { CurveChart, buildCurveSeries, type CurveSeries } from "../../CurveChart";
import type {
  AgentNotificationTargets,
  AgentSnapshotResponse,
  Currency,
  NotificationDestinations,
  RankingEntry,
  SpecialEvent,
} from "../../lib/types";
import { formatDateShort, formatNumber, percentClass, signedPct } from "../../lib/format";
import { AgentNotificationPanel } from "./AgentNotificationPanel";

const ORDERS_PAGE_SIZE = 8;

const SPECIAL_EVENT_LABELS: Record<string, string> = {
  corporate_action: "Corporate Action",
  manual_position_clear: "Manual Clear",
};

type NotifField = keyof AgentNotificationTargets;

type SnapshotPanelProps = {
  snapshot: AgentSnapshotResponse | null;
  loadingSnapshot: boolean;
  selectedRanking: RankingEntry | null;
  specialEvents: SpecialEvent[];
  loadingSpecialEvents: boolean;
  destinations: NotificationDestinations | null;
  agentTargets: AgentNotificationTargets | null;
  savingTargets: boolean;
  onToggleTarget: (field: NotifField, key: string) => void;
  onManualReset: () => void;
  onDelete: () => void;
  symbolHeader: string;
  formatAmount: (value: number | null | undefined, currency: Currency) => string;
  formatYAxisLabel: (value: number, currency: Currency) => string;
  formatDateTime: (value: string | null | undefined) => string;
  formatTime: (value: string | null | undefined) => string;
};

/** The selected agent's full book: header, notifications, stat tiles, equity
 * curve, holdings, orders/fills, and special events. Mount with `key={agentId}`
 * so the orders pager resets when the agent changes. */
export function SnapshotPanel({
  snapshot,
  loadingSnapshot,
  selectedRanking,
  specialEvents,
  loadingSpecialEvents,
  destinations,
  agentTargets,
  savingTargets,
  onToggleTarget,
  onManualReset,
  onDelete,
  symbolHeader,
  formatAmount,
  formatYAxisLabel,
  formatDateTime,
  formatTime,
}: SnapshotPanelProps) {
  const [ordersPage, setOrdersPage] = useState(1);

  const perAgentSeries: CurveSeries[] = useMemo(
    () =>
      snapshot
        ? [
            buildCurveSeries(
              snapshot.agent.agent_id,
              snapshot.agent.display_name,
              snapshot.agent.currency,
              snapshot.agent.initial_cash,
              snapshot.equity,
            ),
          ]
        : [],
    [snapshot],
  );

  const orderedOrders = snapshot ? [...snapshot.operations.orders].reverse() : [];
  const orderedSpecialEvents = [...specialEvents].reverse();
  const fillByOrderId = new Map(
    (snapshot?.operations.fills ?? []).map((fill) => [fill.order_id, fill]),
  );
  const totalOrdersPages = Math.max(1, Math.ceil(orderedOrders.length / ORDERS_PAGE_SIZE));
  const currentOrdersPage = Math.min(ordersPage, totalOrdersPages);
  const visibleOrders = orderedOrders.slice(
    (currentOrdersPage - 1) * ORDERS_PAGE_SIZE,
    currentOrdersPage * ORDERS_PAGE_SIZE,
  );

  return (
    <section className="board-main">
      <div className="snapshot-head">
        <div>
          <h2 className="name">{snapshot?.agent.display_name ?? "Select an Agent"}</h2>
          {snapshot ? (
            <div className="id">
              {snapshot.agent.agent_id} ·{" "}
              {snapshot.agent.ib_mode ? `${snapshot.agent.ib_mode.toUpperCase()} · ` : ""}
              {snapshot.agent.role.toUpperCase()} ·{" "}
              {snapshot.agent.enabled ? "LIVE" : "OFFLINE"}
            </div>
          ) : (
            <div className="id">— pick a name from the roster, the books will open —</div>
          )}
        </div>
        {snapshot && (
          <div className="snapshot-head-right">
            <button className="delete manual-clear-trigger" type="button" onClick={onManualReset}>
              Manual Reset
            </button>
            <button className="delete" type="button" onClick={onDelete}>
              Strike from Book
            </button>
          </div>
        )}
      </div>

      {snapshot && (
        <div className="snapshot-notif-row">
          <AgentNotificationPanel
            destinations={destinations}
            agentTargets={agentTargets}
            saving={savingTargets}
            onToggle={onToggleTarget}
          />
        </div>
      )}

      {loadingSnapshot ? (
        <div className="snapshot-empty">Loading the ledger…</div>
      ) : snapshot ? (
        <>
          <section className="stat-row">
            <article className="stat-tile">
              <p className="label">Total Equity · {snapshot.agent.currency}</p>
              <div className="value">
                {formatAmount(snapshot.portfolio.total_equity, snapshot.agent.currency)}
              </div>
            </article>
            <article className="stat-tile">
              <p className="label">Market Value</p>
              <div className="value">
                {formatAmount(snapshot.portfolio.market_value, snapshot.agent.currency)}
              </div>
            </article>
            <article className="stat-tile">
              <p className="label">Cash</p>
              <div className="value">
                {formatAmount(snapshot.portfolio.cash, snapshot.agent.currency)}
              </div>
            </article>
            <article className="stat-tile">
              <p className="label">Total Return</p>
              <div className={`value ${percentClass(selectedRanking?.return_pct ?? 0)}`}>
                {signedPct(selectedRanking?.return_pct ?? 0)}
              </div>
            </article>
            <article className="stat-tile">
              <p className="label">Today</p>
              <div
                className={`value ${
                  snapshot.portfolio.day_return_pct == null
                    ? "flat"
                    : percentClass(snapshot.portfolio.day_return_pct)
                }`}
              >
                {snapshot.portfolio.day_return_pct == null
                  ? "—"
                  : signedPct(snapshot.portfolio.day_return_pct)}
              </div>
            </article>
            <article className="stat-tile">
              <p className="label">As Of</p>
              <div className="value">{formatDateTime(snapshot.portfolio.as_of)}</div>
            </article>
          </section>

          <section className="equity">
            <div className="equity-chart">
              {snapshot.equity.length >= 2 ? (
                <CurveChart
                  series={perAgentSeries}
                  mode="equity"
                  height={240}
                  formatAmount={formatAmount}
                  formatYAxisLabel={formatYAxisLabel}
                />
              ) : (
                <div className="curve-placeholder">Need at least two equity points</div>
              )}
            </div>
          </section>

          <section className="table-block">
            <div className="table-head">
              <h4>Holdings</h4>
              <div className="table-tools">
                <span>{snapshot.portfolio.positions.length} lines</span>
              </div>
            </div>
            <table>
              <thead>
                <tr>
                  <th>{symbolHeader}</th>
                  <th>Name</th>
                  <th className="num">Qty</th>
                  <th className="num">Sellable</th>
                  <th className="num">Avg</th>
                  <th className="num">Last</th>
                  <th>Updated</th>
                  <th className="num">Value</th>
                  <th className="num">Unrealized</th>
                </tr>
              </thead>
              <tbody>
                {snapshot.portfolio.positions.map((position) => (
                  <tr key={position.code}>
                    <td className="code">{position.code}</td>
                    <td>{position.name ?? "-"}</td>
                    <td className="num">{position.quantity}</td>
                    <td className="num">{position.sellable_quantity}</td>
                    <td className="num">{formatNumber(position.avg_cost, 3)}</td>
                    <td className="num">{formatNumber(position.market_price, 3)}</td>
                    <td>{formatTime(position.intraday_as_of)}</td>
                    <td className="num">
                      {formatAmount(position.market_value, snapshot.agent.currency)}
                    </td>
                    <td className={`num ${percentClass(position.unrealized_pnl)}`}>
                      {formatAmount(position.unrealized_pnl, snapshot.agent.currency)}
                    </td>
                  </tr>
                ))}
                {snapshot.portfolio.positions.length === 0 && (
                  <tr>
                    <td colSpan={9} className="empty">
                      No positions on the book
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </section>

          <section className="table-block">
            <div className="table-head">
              <h4>Orders &amp; Fills</h4>
              <div className="table-tools">
                <span>{snapshot.operations.orders.length} entries</span>
                {snapshot.operations.orders.length > 0 && (
                  <div className="pager" aria-label="Orders pagination">
                    <button
                      type="button"
                      onClick={() => setOrdersPage((page) => Math.max(1, page - 1))}
                      disabled={currentOrdersPage === 1}
                    >
                      ← Prev
                    </button>
                    <span className="pager-label">
                      {currentOrdersPage} / {totalOrdersPages}
                    </span>
                    <button
                      type="button"
                      onClick={() => setOrdersPage((page) => Math.min(totalOrdersPages, page + 1))}
                      disabled={currentOrdersPage === totalOrdersPages}
                    >
                      Next →
                    </button>
                  </div>
                )}
              </div>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>{symbolHeader}</th>
                  <th>Name</th>
                  <th>Side</th>
                  <th className="num">Qty</th>
                  <th className="num">Limit</th>
                  <th className="num">Filled</th>
                  <th>Status</th>
                  <th>Comment</th>
                </tr>
              </thead>
              <tbody>
                {visibleOrders.map((order) => {
                  const fill = fillByOrderId.get(order.order_id);
                  const isFilled = order.filled_at != null;
                  const isCanceled = order.canceled_at != null;
                  return (
                    <tr key={order.order_id}>
                      <td>{formatDateTime(order.submitted_at)}</td>
                      <td className="code">{order.code}</td>
                      <td>{order.name ?? "-"}</td>
                      <td>
                        <span className={`side ${order.side}`}>
                          <span className="indicator" />
                          {order.side}
                        </span>
                      </td>
                      <td className="num">{order.quantity}</td>
                      <td className="num">{formatNumber(order.limit_price, 2)}</td>
                      <td className="num">{fill ? formatNumber(fill.executed_price, 2) : "—"}</td>
                      <td>
                        <div className={`status-cell ${isFilled ? "filled" : ""}`}>
                          {isFilled
                            ? `Filled · ${formatDateTime(order.filled_at)}`
                            : isCanceled
                              ? `Canceled · ${formatDateTime(order.canceled_at)}`
                              : order.status.toUpperCase()}
                        </div>
                        {order.rejection_reason && (
                          <div className="order-meta down">{order.rejection_reason}</div>
                        )}
                      </td>
                      <td className="comment-cell">{order.comment}</td>
                    </tr>
                  );
                })}
                {snapshot.operations.orders.length === 0 && (
                  <tr>
                    <td colSpan={9} className="empty">
                      No orders on record
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </section>

          {(orderedSpecialEvents.length > 0 || loadingSpecialEvents) && (
            <section className="table-block">
              <div className="table-head">
                <h4>Special Events</h4>
                <div className="table-tools">
                  <span>{orderedSpecialEvents.length} entries</span>
                </div>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>{symbolHeader}</th>
                    <th>Type</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {orderedSpecialEvents.map((event) => (
                    <tr key={event.event_id}>
                      <td>{formatDateShort(event.event_date)}</td>
                      <td className="code">{event.code ?? "—"}</td>
                      <td>{SPECIAL_EVENT_LABELS[event.event_type] ?? event.event_type}</td>
                      <td className="comment-cell" style={{ whiteSpace: "pre-line" }}>
                        {event.summary}
                      </td>
                    </tr>
                  ))}
                  {orderedSpecialEvents.length === 0 && (
                    <tr>
                      <td colSpan={4} className="empty">
                        {loadingSpecialEvents ? "Loading…" : "No special events on record"}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </section>
          )}
        </>
      ) : (
        <div className="snapshot-empty">— pick a name from the roster, the books will open —</div>
      )}
    </section>
  );
}
