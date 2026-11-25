import "server-only";

import { cacheLife } from "next/cache";

import { request } from "../backend/request.ts";

import { SearchOutput, SearchRequest } from "./schemas.ts";
import type { TablesResult } from "./tables.ts";

// POST /dev/tools/search_tables (phase 2 search; a development endpoint, api.md 8장) from the server: the search
// page prefetch and the /api/tables proxy. Cached per question for a few minutes, so a repeat search does not run
// the reranker LLM again. A failure throws inside the cache (never cached) and comes out here as a value.
export async function searchTables(question: string): Promise<TablesResult> {
  try {
    return { ok: true, out: await cachedSearch(question) };
  } catch (e) {
    return { ok: false, message: e instanceof Error && e.message ? e.message : "통계표 검색에 실패했습니다." };
  }
}

async function cachedSearch(question: string): Promise<SearchOutput> {
  "use cache";
  cacheLife("minutes");
  // Read per call: a module-level constant would be fixed at build time in the standalone image
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
  const res = await request(`${backend}/dev/tools/search_tables`, {
    method: "POST", body: { question }, bodySchema: SearchRequest, responseSchema: SearchOutput,
  });
  if (!res.ok) {
    throw new Error(res.code === "bad_response" ? "검색 응답 형식 오류" : `검색 서버 오류 (${res.status ?? "연결 실패"})`);
  }
  return res.data;
}
