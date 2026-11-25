import { forward } from "@/lib/backend/forward";
import { FeedbackRequest, FeedbackResponse } from "@/lib/feedback/schemas";

// POST /feedback (api.md 3장) with the browser's login cookie or X-Session-Id: body and answer checked with zod.
export async function POST(request: Request) {
  return forward(request, "/feedback", { bodySchema: FeedbackRequest, responseSchema: FeedbackResponse });
}
