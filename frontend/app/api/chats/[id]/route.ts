import { backendUrl, identity } from "@/lib/auth/backend.server";

// GET /chat/{id} (a member's saved turns) and DELETE /chat/{id} (member: the conversation, guest: the thread of
// X-Session-Id), with the browser's login cookie. Status codes come back as they are (401, 404, 204).
async function forward(request: Request, ctx: RouteContext<"/api/chats/[id]">) {
  const { id } = await ctx.params;
  const res = await fetch(`${backendUrl()}/chat/${encodeURIComponent(id)}`, {
    method: request.method, headers: identity(request), cache: "no-store",
  }).catch(() => null);
  if (!res) return Response.json({ detail: "chat server unreachable" }, { status: 502 });
  if (res.status === 204) return new Response(null, { status: 204 });
  return new Response(res.body, { status: res.status, headers: { "content-type": res.headers.get("content-type") ?? "application/json" } });
}

export { forward as GET, forward as DELETE };
