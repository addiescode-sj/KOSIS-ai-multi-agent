// Where Kakao login starts, and the page it comes back to. Only pages that mean the same after signing in: a guest's
// open conversation is not in the account, and the login and goodbye screens are done with. The backend checks `next` again.
const RETURNABLE = ["/chat", "/settings"];

export function loginHref(next: string | null): string {
  return next && RETURNABLE.includes(next) ? `/auth/kakao/login?next=${encodeURIComponent(next)}` : "/auth/kakao/login";
}
