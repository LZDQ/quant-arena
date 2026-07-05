// One typed API client for the whole frontend.
//
// Replaces the two ad-hoc fetch layers (App.tsx's inline `fetch` calls and
// ArenaDashboard's in-component `apiFetch`) that each resolved the base URL
// their own way. Every endpoint is a typed method here, and the base-URL rule
// lives in exactly one place.

import type {
  AgentCreatedResponse,
  AgentNotificationTargets,
  AgentResponse,
  AgentSnapshotResponse,
  ArenaStatus,
  CreateAgentForm,
  DailyReport,
  DailyReportPage,
  ManualClearForm,
  NapCatTarget,
  NotificationDestinations,
  QQOpenGroupTarget,
  RankingEntry,
  SpecialEvent,
} from "./types";

/** The URL prefix the app is mounted under, read at runtime from the page's
 * `<base href>` tag (the backend rewrites it to its `QUANT_ARENA_URL_PREFIX`
 * when serving index.html). "" at the root, "/quant-arena" when prefixed.
 * Trailing slashes are trimmed so callers can always append "/...". */
export function urlPrefix(): string {
  return new URL(document.baseURI).pathname.replace(/\/+$/, "");
}

/** Resolve the base for API and WebSocket URLs from VITE_API_BASE (build time):
 *   - empty/unset          -> urlPrefix()               (same origin, same mount)
 *   - "/prefix"            -> "/prefix"                 (`/prefix/api/...`)
 *   - "http://host/aaa"    -> "http://host/aaa"         (`http://host/aaa/api/...`)
 * Trailing slashes are trimmed so callers can always prepend "/api...". */
export function resolveApiBase(): string {
  const raw = (import.meta.env.VITE_API_BASE ?? "").trim().replace(/\/+$/, "");
  if (!raw) {
    return urlPrefix();
  }
  if (raw.startsWith("/") || /^https?:\/\//i.test(raw)) {
    return raw;
  }
  return `/${raw}`;
}

/** Same base for WebSocket callers, as an absolute ws(s):// URL prefix. */
export function resolveWsBase(): string {
  const base = resolveApiBase();
  if (/^https?:\/\//i.test(base)) {
    return base.replace(/^http/i, "ws");
  }
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${base}`;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export type ArenaApi = ReturnType<typeof createArenaApi>;

/**
 * Build an API client bound to an arena route prefix (e.g. "" for A-Share,
 * "/futumoo", "/ib"). Global endpoints ignore the prefix; per-arena endpoints
 * mount under `/api${apiPrefix}`. The base URL comes from VITE_API_BASE.
 */
export function createArenaApi(apiPrefix = "") {
  const base = resolveApiBase();

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${base}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
      ...init,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
      throw new ApiError(body.detail ?? `HTTP ${response.status}`, response.status);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return response.json() as Promise<T>;
  }

  const arena = (suffix: string) => `/api${apiPrefix}${suffix}`;

  return {
    base,
    request,

    // --- Global (arena-independent) endpoints ----------------------------
    listArenas: () => request<ArenaStatus[]>(`/api/arenas`),
    setArenaEnabled: (slug: string, enabled: boolean) =>
      request<unknown>(`/api/arenas/${slug}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      }),
    getDestinations: () => request<NotificationDestinations>(`/api/notifications/destinations`),
    putNapCatDestinations: (destinations: Record<string, NapCatTarget>) =>
      request<NotificationDestinations>(`/api/notifications/napcat/destinations`, {
        method: "PUT",
        body: JSON.stringify({ destinations }),
      }),
    putQQOpenDestinations: (destinations: Record<string, QQOpenGroupTarget>) =>
      request<NotificationDestinations>(`/api/notifications/qq-open/destinations`, {
        method: "PUT",
        body: JSON.stringify({ destinations }),
      }),

    // --- Per-arena endpoints ---------------------------------------------
    listAgents: () => request<AgentResponse[]>(arena(`/agents`)),
    getSnapshot: (agentId: string) =>
      request<AgentSnapshotResponse>(arena(`/agents/${agentId}`)),
    createAgent: (form: CreateAgentForm) =>
      request<AgentCreatedResponse>(arena(`/agents`), {
        method: "POST",
        body: JSON.stringify({ ...form, initial_cash: Number(form.initial_cash) }),
      }),
    deleteAgent: (agentId: string) =>
      request<void>(arena(`/agents/${agentId}`), { method: "DELETE" }),
    putNotificationTargets: (agentId: string, targets: AgentNotificationTargets) =>
      request<AgentNotificationTargets>(arena(`/agents/${agentId}/notification-targets`), {
        method: "PUT",
        body: JSON.stringify(targets),
      }),
    manualClear: (agentId: string, form: ManualClearForm) =>
      request<unknown>(arena(`/agents/${agentId}/manual-position-clear`), {
        method: "POST",
        body: JSON.stringify(form),
      }),
    listDailyReports: (agentId: string, page: number, pageSize: number) =>
      request<DailyReportPage>(
        arena(`/agents/${agentId}/daily-reports?page=${page}&page_size=${pageSize}`),
      ),
    getDailyReport: (agentId: string, tradeDate: string) =>
      request<DailyReport>(arena(`/agents/${agentId}/daily-reports/${tradeDate}`)),
    listSpecialEvents: (agentId: string) =>
      request<SpecialEvent[]>(arena(`/agents/${agentId}/special-events`)),
    getRankings: () => request<RankingEntry[]>(arena(`/rankings`)),
  };
}
