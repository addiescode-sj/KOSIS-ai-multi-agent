import { errorResponse } from "@/lib/backend/errors";
import { filterFrom } from "@/lib/admin/filter";
import { adminPreviewOn } from "@/lib/admin/preview";
import { readDashboard } from "@/lib/admin/read.server";

// The admin preview dashboard for the browser query (sample data, T3-63). 404 in a production build.
export async function GET(request: Request) {
  if (!adminPreviewOn()) return errorResponse(404, "not_found", "없는 주소입니다.");
  const result = readDashboard(filterFrom(new URL(request.url).searchParams));
  return result.ok ? Response.json(result.data) : errorResponse(500, "bad_sample", result.message);
}
