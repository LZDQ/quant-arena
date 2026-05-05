import { ArenaDashboard } from "./ArenaDashboard";

const BASE_URL = import.meta.env.BASE_URL.replace(/\/+$/, "");

const amountFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
  minimumFractionDigits: 2,
});

function formatAmount(value: number | null | undefined): string {
  if (value == null) {
    return "--";
  }
  return amountFormatter.format(value);
}

const utcDatetimeFormatter = new Intl.DateTimeFormat("en-US", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return utcDatetimeFormatter.format(new Date(value));
}

function formatYAxisLabel(value: number): string {
  return Math.round(value).toLocaleString("en-US");
}

export function FutumooApp() {
  return (
    <ArenaDashboard
      apiPrefix="/futumoo"
      homeUrl={BASE_URL}
      formatAmount={formatAmount}
      formatYAxisLabel={formatYAxisLabel}
      formatDateTime={formatDateTime}
      masthead={{
        title: (
          <>
            Futu · <em>Moo</em>
          </>
        ),
        glyph: "富",
        han: "富 途 离 线 通 鉴",
        metaLines: [
          "OFFLINE PAPER · BUREAU OF SIMULATED EQUITIES",
          "HK · US · CN VIA FUTU OPEND",
          "FILL-ON-SUBMIT · NO T+1 · MIXED CURRENCY",
        ],
      }}
      symbolHeader="Symbol"
      enlistPlaceholders={{ agentId: "moo-01", displayName: "The Mooing Bull" }}
      confirmDeletePrefix="Delete futumoo agent"
      footer={{
        left: "Composed offline · Bureau of Simulated Equities",
        right: "量化竞技场 · Futu Moo Edition",
      }}
    />
  );
}
