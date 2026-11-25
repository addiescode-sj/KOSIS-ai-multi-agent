import { MemberChatList } from "@/lib/auth/schemas";
import { forward } from "@/lib/backend/forward";

// GET /chat?cursor= (a member's conversations, last asked first) with the browser's login cookie. 401 for a guest.
export async function GET(request: Request) {
  return forward(request, "/chat", { responseSchema: MemberChatList });
}
