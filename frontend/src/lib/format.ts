// Formatting helpers shared across the app.
//
// Previously these were copy-pasted: the currency/date formatters were
// byte-identical in FutumooApp and IBApp, and `todayStamp`, `signedPct`,
// `percentClass` and `formatDateShort` were duplicated between ArenaDashboard
// and CurveChart. They now live here so there is exactly one implementation.

import type { Currency } from "./types";

export function pad2(value: number): string {
  return value.toString().padStart(2, "0");
}

export function formatDateKey(year: number, month: number, day: number): string {
  return `${year}-${pad2(month + 1)}-${pad2(day)}`;
}

/** Fixed-digit number, or "--" for null/undefined. */
export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value == null) {
    return "--";
  }
  return value.toFixed(digits);
}

/** ISO date → "JUN 21" (rendered in the runtime's local timezone). */
export function formatDateShort(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "2-digit" })
    .format(new Date(value))
    .toUpperCase();
}

/** "up" / "down" / "flat" class for a signed number (0 → "flat"). */
export function percentClass(value: number): string {
  if (value > 0) {
    return "up";
  }
  if (value < 0) {
    return "down";
  }
  return "flat";
}

/** Null-aware variant of {@link percentClass}; null and 0 both read "flat". */
export function pctClass(value: number | null): string {
  return value == null ? "flat" : percentClass(value);
}

/** Percent with an explicit "+" for positives (negatives already carry "-"). */
export function signedPct(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export type Stamp = { iso: string; label: string; edition: string; weekday: string };

/** The masthead date stamp: ISO day, long label, weekday, and an edition number
 * counting days since 2025-01-01. */
export function todayStamp(): Stamp {
  const now = new Date();
  const iso = now.toISOString().slice(0, 10);
  const label = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", year: "numeric" })
    .format(now)
    .toUpperCase();
  const weekday = new Intl.DateTimeFormat("en-US", { weekday: "long" }).format(now).toUpperCase();
  const start = new Date("2025-01-01T00:00:00Z").getTime();
  const days = Math.floor((now.getTime() - start) / 86400000) + 1;
  const edition = String(Math.max(days, 1)).padStart(4, "0");
  return { iso, label, edition, weekday };
}

// --- Currency / per-arena formatting factory -----------------------------

const CURRENCY_FORMATTERS: Record<Currency, Intl.NumberFormat> = {
  CNY: new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }),
  HKD: new Intl.NumberFormat("en-HK", { style: "currency", currency: "HKD", maximumFractionDigits: 2 }),
  USD: new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }),
};

const CURRENCY_GLYPH: Record<Currency, string> = {
  CNY: "¥",
  HKD: "HK$",
  USD: "$",
};

export type ArenaFormatting = {
  formatAmount: (value: number | null | undefined, currency: Currency) => string;
  formatYAxisLabel: (value: number, currency: Currency) => string;
  formatDateTime: (value: string | null | undefined) => string;
  formatTime: (value: string | null | undefined) => string;
};

export type ArenaFormattingOptions = {
  /** BCP-47 locale for the date/time formatters. A-Share uses "zh-CN" (24h),
   * the OpenD/Gateway arenas use "en-US" (12h). */
  dateLocale?: string;
  /** Pin the date/time formatters to a timezone (e.g. "UTC"); omit for local. */
  timeZone?: string;
};

/**
 * Build the four currency-aware formatters an arena needs. Collapses the
 * previously-identical FutumooApp/IBApp blocks and AShareApp's near-identical
 * one into a single configurable factory.
 */
export function createArenaFormatting(options: ArenaFormattingOptions = {}): ArenaFormatting {
  const dateLocale = options.dateLocale ?? "en-US";
  const tz = options.timeZone ? { timeZone: options.timeZone } : {};

  const datetimeFormatter = new Intl.DateTimeFormat(dateLocale, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    ...tz,
  });
  const timeFormatter = new Intl.DateTimeFormat(dateLocale, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...tz,
  });

  return {
    formatAmount: (value, currency) =>
      value == null ? "--" : CURRENCY_FORMATTERS[currency].format(value),
    formatYAxisLabel: (value, currency) =>
      `${CURRENCY_GLYPH[currency]}${Math.round(value).toLocaleString("en-US")}`,
    formatDateTime: (value) => (value ? datetimeFormatter.format(new Date(value)) : "--"),
    formatTime: (value) => (value ? timeFormatter.format(new Date(value)) : "--"),
  };
}
