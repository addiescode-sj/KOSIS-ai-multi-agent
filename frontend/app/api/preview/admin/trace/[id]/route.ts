import { errorResponse } from "@/lib/backend/errors";
import { adminPreviewOn } from "@/lib/admin/preview";
import { readTrace } from "@/lib/admin/read.server";

// One request's spans for the admin preview trace screen (sample data, T3-63). 404 in a production build.
export async function GET(_request: Request, { params }: RouteContext<"/api/preview/admin/trace/[id]">) {
  if (!adminPreviewOn()) return errorResponse(404, "not_found", "없는 주소입니다.");
  const result = readTrace((await params).id);
  return result.ok ? Response.json(result.data) : errorResponse(500, "bad_sample", result.message);
}
