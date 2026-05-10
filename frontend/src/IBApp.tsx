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

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return utcDatetimeFormatter.format(new Date(value));
}

function formatYAxisLabel(value: number, currency: Currency): string {
  return `${currencyGlyph[currency]}${Math.round(value).toLocaleString("en-US")}`;
}

export function IBApp() {
  return (
    <ArenaDashboard
      apiPrefix="/ib"
      homeUrl={BASE_URL}
      formatAmount={formatAmount}
      formatYAxisLabel={formatYAxisLabel}
      formatDateTime={formatDateTime}
      masthead={{
        title: (
          <>
            Inter · <em>Brokers</em>
          </>
        ),
        glyph: "盈",
        han: "盈 透 网 关 通 鉴",
        metaLines: [
          "GATEWAY-BACKED · BUREAU OF SIMULATED EQUITIES",
          "PAPER + REAL · HK · US · MULTI-CCY",
          "ONE PAPER AGENT · ONE REAL AGENT · NO MORE",
        ],
      }}
      symbolHeader="Symbol"
      enlistPlaceholders={{ agentId: "ib-paper", displayName: "The Gateway Sentinel" }}
      confirmDeletePrefix="Delete IB agent"
      currencyOptions={[
        { value: "USD", label: "USD · Account Base" },
        { value: "HKD", label: "HKD · Account Base" },
      ]}
      ibModeOptions={[
        { value: "paper", label: "Paper · ports 4002 / 7497" },
        { value: "real", label: "Real · ports 4001 / 7496" },
      ]}
      footer={{
        left: "Routed live · Bureau of Simulated Equities",
        right: "量化竞技场 · Interactive Brokers Edition",
      }}
    />
  );
}
