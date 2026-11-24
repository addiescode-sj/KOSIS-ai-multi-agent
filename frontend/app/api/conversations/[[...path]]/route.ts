import { backendPath, proxy } from "@/lib/backend/proxy";

// A member's conversations: list, reopen, delete (backend /chat, /chat/{id}; those paths are pages here)
type Context = { params: Promise<{ path?: string[] }> };

async function handle(request: Request, { params }: Context) {
  const path = (await params).path ?? [];
  if (path.length > 1) return new Response(null, { status: 404 });
  return proxy(request, backendPath("/chat", path));
}

export { handle as GET, handle as DELETE };
