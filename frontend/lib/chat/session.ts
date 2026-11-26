import { z } from "zod";

import { persisted } from "../storage/persisted.ts";

// The guest session id the backend keys threads, feedback and deletes on (X-Session-Id, api.md 0·1장): a random
// UUID made once per tab. It identifies no person. Kept in this tab's sessionStorage with the conversations it opened
// (a guest's list is per tab, api.md 1장); an id kept in localStorage before T3-55 moves here once.
export const SESSION_KEY = "kosis.session.v1";

const SessionId = z.uuid().nullable();

const session = persisted(SESSION_KEY, SessionId, { area: "session", fallback: null, legacyArea: "local" });

export function guestSessionId(): string | null {
  if (typeof window === "undefined") return null;
  let id = session.get();
  if (!id) {
    id = crypto.randomUUID();
    session.set(id);
  }
  return id;
}

export function sessionHeaders(): Record<string, string> {
  const id = guestSessionId();
  return id ? { "x-session-id": id } : {};
}
