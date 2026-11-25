import { forward } from "@/lib/backend/forward";
import { EventsRequest, EventsResponse } from "@/lib/events/schemas";

// POST /events (api.md 5장): a batch of screen events, also sent with navigator.sendBeacon when the page goes away.
export async function POST(request: Request) {
  return forward(request, "/events", { bodySchema: EventsRequest, responseSchema: EventsResponse });
}
