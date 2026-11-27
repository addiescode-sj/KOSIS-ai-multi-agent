import { queryOptions } from "@tanstack/react-query";

import { request } from "../backend/request.ts";
import { queryKeys } from "../query/keys.ts";

import { filterQuery } from "./filter.ts";
import { AdminDashboard, AdminTrace, type AdminFilter } from "./schemas.ts";

// The admin preview queries, shared by the server prefetch (lib/admin/read.server) and the browser
// (useSuspenseQuery) so both use one key. A failure is a value, not a throw (AGENTS.md).
export type AdminResult<T> = { ok: true; data: T } | { ok: false; message: string };

export const dashboardPath = (f: AdminFilter) => `/api/preview/admin/dashboard?${filterQuery(f)}`;
export const tracePath = (id: string) => `/api/preview/admin/trace/${encodeURIComponent(id)}`;

const FRESH_MS = 60_000; // the metrics batch runs hourly: a minute old is still the same numbers

export function dashboardQuery(f: AdminFilter) {
  return queryOptions({
    queryKey: queryKeys.adminDashboard(f),
    queryFn: ({ signal }) => read(dashboardPath(f), AdminDashboard, signal),
    staleTime: FRESH_MS,
  });
}

export function traceQuery(id: string) {
  return queryOptions({
    queryKey: queryKeys.adminTrace(id),
    queryFn: ({ signal }) => read(tracePath(id), AdminTrace, signal),
    staleTime: Infinity, // a finished request's spans do not change
  });
}

async function read<T>(path: string, schema: Parameters<typeof request<T>>[1]["responseSchema"], signal: AbortSignal): Promise<AdminResult<T>> {
  const res = await request(path, { responseSchema: schema, signal });
  if (res.ok) return { ok: true, data: res.data };
  if (res.code === "aborted") throw new Error(res.message); // a cancelled query is not a failed read
  return { ok: false, message: res.message };
}
