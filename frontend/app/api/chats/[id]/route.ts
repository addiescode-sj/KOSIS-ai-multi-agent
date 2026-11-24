import { backendPath, proxy } from "@/lib/backend/proxy";

// GET /chat/{id} (a member's saved turns) and DELETE /chat/{id} (member: the conversation, guest: the thread of
// X-Session-Id). Status codes come back as they are (401, 404, 204).
async function handle(request: Request, { params }: RouteContext<"/api/chats/[id]">) {
  return proxy(request, backendPath("/chat", [(await params).id]));
}

export { handle as GET, handle as DELETE };
