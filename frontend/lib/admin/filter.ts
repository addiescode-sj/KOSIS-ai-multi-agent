import { AdminFilter, Period, UserKind } from "./schemas.ts";

// The dashboard filter lives in the address (?period=&user=&internal=), so the filter screen, a reload and a shared
// link all show the same numbers. Anything missing or unknown falls back to the default. The real metrics stop at
// yesterday (a day's numbers are fixed once it ends, log_metrics MetricFilter.check), so every period ends there.

export const DEFAULT_FILTER: AdminFilter = { period: "7d", user: "all", internal: false }; // as the T3-57 page

export const PERIOD_LABEL: Record<Period, string> = { yesterday: "어제", "7d": "최근 7일", "30d": "최근 30일" };
export const USER_LABEL: Record<UserKind, string> = { all: "전체", member: "회원", guest: "비회원" };
const PERIOD_DAYS: Record<Period, number> = { yesterday: 1, "7d": 7, "30d": 30 };

type Params = Record<string, string | string[] | undefined> | URLSearchParams;

function one(params: Params, key: string): string | undefined {
  const v = params instanceof URLSearchParams ? params.get(key) : params[key];
  return (Array.isArray(v) ? v[0] : v) ?? undefined;
}

export function filterFrom(params: Params): AdminFilter {
  const period = Period.safeParse(one(params, "period"));
  const user = UserKind.safeParse(one(params, "user"));
  const internal = one(params, "internal");
  return {
    period: period.success ? period.data : DEFAULT_FILTER.period,
    user: user.success ? user.data : DEFAULT_FILTER.user,
    internal: internal === "0" ? false : internal === "1" ? true : DEFAULT_FILTER.internal,
  };
}

export function filterQuery(f: AdminFilter): string {
  return new URLSearchParams({ period: f.period, user: f.user, internal: f.internal ? "1" : "0" }).toString();
}

/** Today in Korea as YYYY-MM-DD (the metrics count days in KST). */
export function kstToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(now);
}

function addDays(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** The KST date range of a period: it ends yesterday and spans the period's days. */
export function rangeOf(period: Period, today: string): { start: string; end: string } {
  const end = addDays(today, -1);
  return { start: addDays(end, -(PERIOD_DAYS[period] - 1)), end };
}

/** GET /admin/api/metrics query of a filter (the operator app's own parameter names) */
export function apiQuery(f: AdminFilter, today: string): string {
  const { start, end } = rangeOf(f.period, today);
  return new URLSearchParams({ start, end, user: f.user, internal: f.internal ? "true" : "false" }).toString();
}
