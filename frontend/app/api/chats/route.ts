import { backendUrl, identity } from "@/lib/auth/backend.server";

// GET /chat (a member's conversations, newest first) with the browser's login cookie. 401 for a guest.
export async function GET(request: Request) {
  const cursor = new URL(request.url).searchParams.get("cursor");
  const res = await fetch(`${backendUrl()}/chat${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`, {
    headers: identity(request), cache: "no-store",
  }).catch(() => null);
  if (!res) return Response.json({ detail: "chat server unreachable" }, { status: 502 });
  return new Response(res.body, { status: res.status, headers: { "content-type": res.headers.get("content-type") ?? "application/json" } });
}
