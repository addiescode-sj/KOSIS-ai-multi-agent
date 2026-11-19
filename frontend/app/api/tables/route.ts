import { searchTables } from "@/lib/search/search-tables.server";

// Proxy to POST /tools/search_tables for the browser (the search page's client query). Same cached call as the
// server prefetch, so a search the page already ran does not reach the backend again.
export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const question = typeof body?.question === "string" ? body.question.trim() : "";
  if (!question) return Response.json({ error: "empty question" }, { status: 400 });
  const result = await searchTables(question);
  return result.ok ? Response.json(result.out) : Response.json({ error: result.message }, { status: 502 });
}
