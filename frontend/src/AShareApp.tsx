import { ArenaDashboard } from "./ArenaDashboard";

const BASE_URL = import.meta.env.BASE_URL.replace(/\/+$/, "");

const cnyFormatter = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  maximumFractionDigits: 2,
});

function formatMoney(value: number | null | undefined): string {
  if (value == null) {
    return "--";
  }
  return cnyFormatter.format(value);
}

const datetimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return datetimeFormatter.format(new Date(value));
}

function formatYAxisLabel(value: number): string {
  return `¥${Math.round(value).toLocaleString("en-US")}`;
}

export function AShareApp() {
  return (
    <ArenaDashboard
      apiPrefix=""
      homeUrl={BASE_URL}
      formatAmount={(value) => formatMoney(value)}
      formatYAxisLabel={(value) => formatYAxisLabel(value)}
      formatDateTime={formatDateTime}
      masthead={{
        title: (
          <>
            A · <em>Share</em>
          </>
        ),
        glyph: "沪",
        han: "沪 深 京 通 鉴",
        metaLines: [
          "BUREAU OF SIMULATED EQUITIES",
          "SHANGHAI · SHENZHEN · BEIJING",
          "SETTLEMENT T+1 · STAMP 0.05% · COMM 0.025%",
        ],
      }}
      symbolHeader="Code"
      enlistPlaceholders={{ agentId: "trader-01", displayName: "The Iron Pen" }}
      confirmDeletePrefix="Delete agent"
      currencyOptions={[{ value: "CNY", label: "RMB" }]}
      footer={{
        left: "Composed nightly · Bureau of Simulated Equities",
        right: "量化竞技场 · A-Share Edition",
      }}
    />
  );
}
