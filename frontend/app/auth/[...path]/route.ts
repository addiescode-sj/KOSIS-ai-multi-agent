import { Me, MeUpdate } from "@/lib/auth/schemas";
import { forward } from "@/lib/backend/forward";
import { backendPath, proxy } from "@/lib/backend/proxy";

// Kakao login, logout, my account (docs/service/api.md 6). Start login with a plain <a href="/auth/kakao/login?next=...">:
// a prefetching <Link> would start a login nobody asked for. Redirects and Set-Cookie pass through untouched
// (proxy); the one call with a body, PATCH /auth/me, is checked with zod both ways.
type Context = { params: Promise<{ path: string[] }> };

async function handle(request: Request, { params }: Context) {
  return proxy(request, backendPath("/auth", (await params).path));
}

async function patch(request: Request, { params }: Context) {
  const path = (await params).path;
  if (path.length === 1 && path[0] === "me") return forward(request, "/auth/me", { bodySchema: MeUpdate, responseSchema: Me });
  return proxy(request, backendPath("/auth", path));
}

export { handle as GET, handle as POST, patch as PATCH, handle as DELETE };
