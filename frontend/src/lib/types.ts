// Shared domain types for the Quant Arena frontend.
//
// These mirror the backend Pydantic schemas (quant_arena/schemas.py) one-to-one.
// They live here — not next to a single component — so the dashboard, the market
// picker and the chart all speak the same vocabulary and a backend field rename
// only has to be reflected in one place.

export type Currency = "CNY" | "HKD" | "USD";

export type IBMode = "paper" | "real";

export type AgentResponse = {
  agent_id: string;
  display_name: string;
  initial_cash: number;
  currency: Currency;
  enabled: boolean;
  role: "normal" | "monitor";
  ib_mode: IBMode | null;
  napcat_notify_targets: string[];
  qq_open_notify_targets: string[];
  daily_report_notify_targets: string[];
};

export type AgentCreatedResponse = {
  agent: AgentResponse;
  token_secret: string;
};

export type PositionView = {
  code: string;
  name: string | null;
  quantity: number;
  sellable_quantity: number;
  avg_cost: number;
  market_price: number | null;
  market_value: number;
  unrealized_pnl: number;
  intraday_as_of: string | null;
};

export type OrderRecord = {
  order_id: string;
  code: string;
  name: string | null;
  side: "buy" | "sell";
  quantity: number;
  limit_price: number;
  comment: string;
  status: string;
  submitted_at: string;
  filled_at: string | null;
  canceled_at: string | null;
  rejection_reason: string | null;
};

export type FillRecord = {
  fill_id: string;
  order_id: string;
  code: string;
  side: "buy" | "sell";
  quantity: number;
  executed_at: string;
  executed_price: number;
  commission: number;
  stamp_tax: number;
};

export type PortfolioResponse = {
  agent_id: string;
  currency: Currency;
  cash: number;
  market_value: number;
  total_equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  positions: PositionView[];
  pending_orders: OrderRecord[];
  as_of: string | null;
  day_return_pct: number | null;
};

export type OperationListResponse = {
  orders: OrderRecord[];
  fills: FillRecord[];
};

export type EquityPoint = {
  trade_date: string;
  cash: number;
  market_value: number;
  total_equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
};

export type SpecialEvent = {
  event_id: string;
  event_type: string;
  event_date: string;
  code: string | null;
  summary: string;
  occurred_at: string;
};

export type AgentSnapshotResponse = {
  agent: AgentResponse;
  portfolio: PortfolioResponse;
  operations: OperationListResponse;
  equity: EquityPoint[];
};

export type RankingEntry = {
  trade_date: string;
  agent_id: string;
  display_name: string;
  currency: Currency;
  cash: number;
  market_value: number;
  total_equity: number;
  return_pct: number;
  realized_pnl: number;
  unrealized_pnl: number;
};

export type DailyReportSummary = {
  trade_date: string;
  updated_at: string;
};

export type DailyReport = {
  trade_date: string;
  content: string;
  updated_at: string;
};

export type DailyReportPage = {
  items: DailyReportSummary[];
  total: number;
  page: number;
  page_size: number;
};

// --- Notifications -------------------------------------------------------

export type NapCatPrivateTarget = { type: "private"; user_id: string };
export type NapCatGroupTarget = { type: "group"; group_id: string };
export type NapCatTarget = NapCatPrivateTarget | NapCatGroupTarget;
export type QQOpenGroupTarget = { type: "group"; group_openid: string };

export type NotificationDestinations = {
  napcat_enabled: boolean;
  napcat_destinations: Record<string, NapCatTarget>;
  qq_open_enabled: boolean;
  qq_open_destinations: Record<string, QQOpenGroupTarget>;
};

export type AgentNotificationTargets = {
  napcat: string[];
  qq_open: string[];
  daily_report: string[];
};

export type ArenaStatus = { slug: string; label: string; enabled: boolean };

// --- Form / draft view-models -------------------------------------------

export type CreateAgentForm = {
  agent_id: string;
  display_name: string;
  initial_cash: string;
  currency: Currency;
  role: "normal" | "monitor";
  ib_mode: IBMode | null;
};

export type ManualClearForm = {
  comment: string;
  keep_unrealized_pnl: boolean;
  keep_realized_pnl: boolean;
};

export type CurrencyOption = {
  /** Backend currency code, e.g. "CNY", "HKD", "USD". */
  value: Currency;
  /** Label shown in the form (typically same as value, but may diverge). */
  label: string;
};

export type IBModeOption = {
  value: IBMode;
  label: string;
};

export type NapCatDraft = {
  key: string;
  type: "private" | "group";
  user_id: string;
  group_id: string;
};

export type QQOpenDraft = {
  key: string;
  group_openid: string;
};
