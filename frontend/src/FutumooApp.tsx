import { ArenaDashboard } from "./ArenaDashboard";

const BASE_URL = import.meta.env.BASE_URL.replace(/\/+$/, "");

type Currency = "CNY" | "HKD" | "USD";

const currencyFormatters: Record<Currency, Intl.NumberFormat> = {
  CNY: new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }),
  HKD: new Intl.NumberFormat("en-HK", { style: "currency", currency: "HKD", maximumFractionDigits: 2 }),
  USD: new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }),
};

const currencyGlyph: Record<Currency, string> = {
  CNY: "¥",
  HKD: "HK$",
  USD: "$",
};

function formatAmount(value: number | null | undefined, currency: Currency): string {
  if (value == null) {
    return "--";
  }
  return currencyFormatters[currency].format(value);
}

const utcDatetimeFormatter = new Intl.DateTimeFormat("en-US", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});

const utcTimeFormatter = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return utcDatetimeFormatter.format(new Date(value));
}

function formatTime(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return utcTimeFormatter.format(new Date(value));
}

function formatYAxisLabel(value: number, currency: Currency): string {
  return `${currencyGlyph[currency]}${Math.round(value).toLocaleString("en-US")}`;
}

export function FutumooApp() {
  return (
    <ArenaDashboard
      apiPrefix="/futumoo"
      homeUrl={BASE_URL}
      formatAmount={formatAmount}
      formatYAxisLabel={formatYAxisLabel}
      formatDateTime={formatDateTime}
      formatTime={formatTime}
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
          "HK · US VIA FUTU OPEND",
          "ONE CURRENCY PER AGENT · NO T+1",
        ],
      }}
      symbolHeader="Symbol"
      enlistPlaceholders={{ agentId: "moo-01", displayName: "The Mooing Bull" }}
      confirmDeletePrefix="Delete futumoo agent"
      currencyOptions={[
        { value: "HKD", label: "HKD · Hong Kong Dollar" },
        { value: "USD", label: "USD · US Dollar" },
      ]}
      footer={{
        left: "Composed offline · Bureau of Simulated Equities",
        right: "量化竞技场 · Futu Moo Edition",
      }}
    />
  );
}
