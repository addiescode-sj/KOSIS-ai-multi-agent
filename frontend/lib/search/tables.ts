import { queryOptions } from "@tanstack/react-query";

import type { SearchOutput, TableCandidate } from "./schemas.ts";

// The search page's table query, shared by the server prefetch (app/search/page.tsx) and the browser
// (useSuspenseQuery) so both use one key. An expected failure is a value, not a throw: a thrown error crossing
// from the server render to the browser loses its message in production.
export type TablesResult = { ok: true; out: SearchOutput } | { ok: false; message: string };

const FRESH_MS = 5 * 60_000;

export function tablesQuery(q: string) {
  return queryOptions({
    queryKey: ["tables", q] as const,
    queryFn: ({ signal }) => fetchTables(q, signal),
    // A found result stays fresh for 5 minutes; a failure is retried on the next visit
    staleTime: (query) => (query.state.data?.ok ? FRESH_MS : 0),
  });
}

async function fetchTables(q: string, signal: AbortSignal): Promise<TablesResult> {
  try {
    const res = await fetch("/api/tables", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question: q }),
      signal,
    });
    const body = await res.json();
    return res.ok ? { ok: true, out: body } : { ok: false, message: body.error ?? "통계표 검색에 실패했습니다." };
  } catch (e) {
    if (signal.aborted) throw e; // a cancelled query is not a failed search
    return { ok: false, message: `통계표 검색에 실패했습니다 (${e})` };
  }
}

// Picked tables first, then the other candidates without repeating a picked one.
export function mergeTables(out: SearchOutput): TableCandidate[] {
  const picked = new Set(out.picked.map((t) => t.key));
  return [...out.picked, ...out.candidates.filter((c) => !picked.has(c.key))];
}
