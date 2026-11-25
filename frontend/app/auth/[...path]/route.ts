import { backendPath, proxy } from "@/lib/backend/proxy";

// Kakao login, logout, my account (docs/service/api.md 6). Start login with a plain <a href="/auth/kakao/login?next=...">:
// a prefetching <Link> would start a login nobody asked for.
type Context = { params: Promise<{ path: string[] }> };

async function handle(request: Request, { params }: Context) {
  return proxy(request, backendPath("/auth", (await params).path));
}

export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
