// Who may see an /admin screen (T3-67), decided before anything is drawn: frontend/proxy.ts asks the operator app's
// GET /admin/api/me with the caller's login cookie. Only an operator gets a screen; anyone else gets a redirect to the
// Kakao login or an empty answer, so the operator screens are not even told to exist (as the T3-57 app did).

// backend/app/auth.py cookie_name(): "__Host-" when the site is served over https
export const LOGIN_COOKIES = ["kosis_session", "__Host-kosis_session"] as const;

// Every /admin screen answer: never stored, never indexed, never framed, no address passed on
export const ADMIN_HEADERS: Record<string, string> = {
  "cache-control": "no-store",
  "x-robots-tag": "noindex, nofollow",
  "x-frame-options": "DENY",
  "referrer-policy": "no-referrer",
};

export type Gate = { kind: "pass" } | { kind: "login" } | { kind: "block"; status: 404 | 503 };

/** The operator app's address inside the cluster (read per request: a module constant is fixed at build time). */
export const adminUrl = () => process.env.ADMIN_URL ?? "http://localhost:8100";

/** What GET /admin/api/me's status means for the screen. null: no answer in time. Anything but 200, 401, 404 is
 * "cannot tell" and shows nothing (a database outage must not show the screens to everyone). */
export function gateFor(meStatus: number | null): Gate {
  if (meStatus === 200) return { kind: "pass" };
  if (meStatus === 401) return { kind: "login" };
  if (meStatus === 404) return { kind: "block", status: 404 };
  return { kind: "block", status: 503 };
}

/** The Kakao login that comes back to this screen (the backend's safe_return accepts /admin paths). */
export const loginLocation = (pathAndQuery: string) => `/auth/kakao/login?next=${encodeURIComponent(pathAndQuery)}`;
