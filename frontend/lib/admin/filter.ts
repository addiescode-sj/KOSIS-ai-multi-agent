import { AdminFilter, Period, UserKind } from "./schemas.ts";

// The dashboard filter lives in the address (?period=&user=&internal=), so the filter screen, a reload and a shared
// link all show the same numbers. Anything missing or unknown falls back to the default.

export const DEFAULT_FILTER: AdminFilter = { period: "today", user: "all", internal: true };

export const PERIOD_LABEL: Record<Period, string> = { today: "오늘", "7d": "최근 7일", "30d": "최근 30일" };
export const USER_LABEL: Record<UserKind, string> = { all: "전체", member: "회원", guest: "비회원" };

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
