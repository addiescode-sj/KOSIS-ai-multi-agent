// The guest session id the backend keys threads, feedback and deletes on (X-Session-Id, contract 1장): a random
// UUID made once per browser. It identifies no person. Kept with the conversations it opened (localStorage).
const KEY = "kosis.session.v1";

export function guestSessionId(): string | null {
  if (typeof window === "undefined") return null;
  let id = window.localStorage.getItem(KEY);
  if (!id) {
    id = crypto.randomUUID();
    window.localStorage.setItem(KEY, id);
  }
  return id;
}

export function sessionHeaders(): Record<string, string> {
  const id = guestSessionId();
  return id ? { "x-session-id": id } : {};
}
