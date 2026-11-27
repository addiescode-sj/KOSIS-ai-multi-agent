import { queryOptions } from "@tanstack/react-query";
import type { z } from "zod";

import { request } from "../backend/request.ts";
import { queryKeys } from "../query/keys.ts";

import { apiQuery, kstToday } from "./filter.ts";
import { AdminMetrics, type AdminFilter } from "./schemas.ts";
import { Home, Inbox, QuestionDetail, QuestionList, ReviewInput, ReviewSaved, type Review } from "./screens.ts";
import { toView, type DashboardView } from "./view.ts";

// The operator screens' reads, from the browser to the operator app on the same origin (T3-66, T3-67): the login
// cookie goes along and the frontend server never handles it (deployed: the ingress sends /admin/api/* there; next
// dev and a local `next start` with ADMIN_URL: next.config.ts rewrites it). A failure is a value, not a throw
// (AGENTS.md); 401 and 404 are screens, not errors to retry.

// Why a screen has no data: the login ended, not an operator (any more), or anything else
export const DashboardFailure = { LOGIN: "login", FORBIDDEN: "forbidden", ERROR: "error" } as const;
export type DashboardFailure = (typeof DashboardFailure)[keyof typeof DashboardFailure];
export type AdminRead<T> = { ok: true; data: T } | { ok: false; failure: DashboardFailure; message: string };
export type DashboardResult = AdminRead<DashboardView>;

const API = "/admin/api";

async function read<T>(path: string, schema: z.ZodType<T>, signal?: AbortSignal): Promise<AdminRead<T>> {
  const res = await request(`${API}${path}`, { responseSchema: schema, signal, init: { redirect: "manual", credentials: "same-origin" } });
  if (res.ok) return { ok: true, data: res.data };
  if (res.code === "aborted") throw new Error(res.message); // a cancelled query is not a failed read
  if (res.status === 401) return { ok: false, failure: DashboardFailure.LOGIN, message: res.message };
  if (res.status === 404) return { ok: false, failure: DashboardFailure.FORBIDDEN, message: res.message || "찾지 못했습니다." };
  return { ok: false, failure: DashboardFailure.ERROR, message: res.message };
}

const FRESH = { staleTime: 60_000, retry: false } as const; // the operator app caches metrics 5 minutes itself

export function dashboardQuery(f: AdminFilter) {
  return queryOptions({ queryKey: queryKeys.adminDashboard(f), queryFn: ({ signal }) => readDashboard(f, signal), ...FRESH });
}

export async function readDashboard(f: AdminFilter, signal?: AbortSignal): Promise<DashboardResult> {
  const res = await read(`/metrics?${apiQuery(f, kstToday())}`, AdminMetrics, signal);
  return res.ok ? { ok: true, data: toView(res.data) } : res;
}

export function homeQuery() {
  return queryOptions({ queryKey: queryKeys.adminHome, queryFn: ({ signal }) => read("/home", Home, signal), ...FRESH, refetchInterval: 60_000 });
}

/** `qs`: the screen's own address query (start, end, outcome, user, internal, page_no), passed on as it is */
export function questionsQuery(qs: string) {
  return queryOptions({ queryKey: queryKeys.adminQuestions(qs), queryFn: ({ signal }) => read(`/questions?${qs}`, QuestionList, signal), ...FRESH });
}

// Opening a question writes one audit row in the operator app, so it is read once per visit: never again on focus,
// on a retry or while it is in the cache.
export function questionQuery(id: string) {
  return queryOptions({
    queryKey: queryKeys.adminQuestion(id),
    queryFn: ({ signal }) => read(`/questions/${encodeURIComponent(id)}`, QuestionDetail, signal),
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

/** `qs`: the inbox address query (status, start, end, category, assignee, page_no) */
export function inboxQuery(qs: string) {
  return queryOptions({ queryKey: queryKeys.adminInbox(qs), queryFn: ({ signal }) => read(`/feedback?${qs}`, Inbox, signal), ...FRESH });
}

export type SaveResult = { ok: true; review: Review } | { ok: false; conflict: boolean; message: string };

/** POST /admin/api/feedback/{id}: JSON, with the operator's form token (the operator app also checks the Origin). */
export async function saveReview(id: string, body: ReviewInput, token: string): Promise<SaveResult> {
  const res = await request(`${API}/feedback/${encodeURIComponent(id)}`, {
    method: "POST", body, bodySchema: ReviewInput, responseSchema: ReviewSaved,
    headers: { "x-admin-token": token }, init: { redirect: "manual", credentials: "same-origin" },
  });
  if (res.ok) return { ok: true, review: res.data.review };
  return { ok: false, conflict: res.status === 409, message: res.message };
}
