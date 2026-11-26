import { MemberChat } from "@/lib/auth/schemas";
import { forward } from "@/lib/backend/forward";
import { backendPath } from "@/lib/backend/proxy";
import { NoContent } from "@/lib/backend/request";

// GET /chat/{id} (a member's saved turns) and DELETE /chat/{id} (member: the conversation, guest: the thread of
// X-Session-Id). Status codes come back as they are (401, 404, 204); a body of another shape is a 502.
type Context = RouteContext<"/api/chats/[id]">;

export async function GET(request: Request, { params }: Context) {
  return forward(request, backendPath("/chat", [(await params).id]), { responseSchema: MemberChat });
}

export async function DELETE(request: Request, { params }: Context) {
  return forward(request, backendPath("/chat", [(await params).id]), { responseSchema: NoContent });
}
