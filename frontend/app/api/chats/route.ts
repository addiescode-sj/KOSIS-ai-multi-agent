import { proxy } from "@/lib/backend/proxy";

// GET /chat (a member's conversations, last asked first) with the browser's login cookie. 401 for a guest.
export async function GET(request: Request) {
  return proxy(request, "/chat");
}
