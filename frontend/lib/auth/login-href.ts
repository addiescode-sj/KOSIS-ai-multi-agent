// Where Kakao login starts, and the page it comes back to. Only pages that mean the same after signing in: a guest's
// open conversation is not in the account, and the login and goodbye screens are done with. The operator screens
// (/admin, T3-67) come back with their filter. The backend checks `next` again.
const RETURNABLE = ["/chat", "/settings"];
const ADMIN_SCREEN = /^\/admin(?:\/(?!api(?:\/|$))[\w\-/]*)?(?:\?[\w\-=&%.]*)?$/;

export function loginHref(next: string | null): string {
  const ok = next !== null && (RETURNABLE.includes(next) || ADMIN_SCREEN.test(next));
  return ok ? `/auth/kakao/login?next=${encodeURIComponent(next)}` : "/auth/kakao/login";
}
