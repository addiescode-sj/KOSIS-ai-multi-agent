import { readBody } from "@/lib/backend/forward";
import { errorResponse } from "@/lib/backend/errors";
import { SearchRequest } from "@/lib/search/schemas";
import { searchTables } from "@/lib/search/search-tables.server";

// Proxy to POST /dev/tools/search_tables for the browser (the search page's client query). Same cached call as the
// server prefetch, so a search the page already ran does not reach the backend again. The body is checked first;
// the backend's answer was checked with SearchOutput in searchTables.
export async function POST(request: Request) {
  const body = await readBody(request, SearchRequest);
  if (!body.ok) return body.response;
  const result = await searchTables(body.data.question);
  return result.ok ? Response.json(result.out) : errorResponse(502, "bad_backend_response", result.message);
}
