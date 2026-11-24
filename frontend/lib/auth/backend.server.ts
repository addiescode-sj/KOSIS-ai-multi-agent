import "server-only";

// The FastAPI address, read per request: a module-level constant would be fixed at build time in the standalone image
export const backendUrl = () => process.env.BACKEND_URL ?? "http://localhost:8000";

// What the backend needs to know who is asking: the login cookie (member) and X-Session-Id (guest, contract 1장)
export function identity(request: Request): Record<string, string> {
  const headers: Record<string, string> = {};
  const cookie = request.headers.get("cookie");
  const session = request.headers.get("x-session-id");
  if (cookie) headers.cookie = cookie;
  if (session) headers["x-session-id"] = session;
  return headers;
}
