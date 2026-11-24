import type { Me } from "../lib/auth/schemas.ts";

// Who is using the app, from GET /auth/me (lib/auth/me.ts):
//   member  — logged in with Kakao; conversations live on the server (GET /chat)
//   guest   — not logged in (401), or the backend has no /auth yet (404) or cannot be reached: chat still works
//   error   — /auth/me failed on the server side (5xx, bad body): shown as a notice, never silently as a guest
//   loading — not known yet
export type AuthState =
  | { status: "loading" }
  | { status: "member"; me: Me }
  | { status: "guest" }
  | { status: "error"; message: string };

// Settings the contract has no API for yet (docs/service/api.md 6장): kept in this browser until the backend has them.
export type AvatarColor = "blue" | "green" | "amber" | "rose" | "slate";
export type Retention = "90일 후 삭제" | "30일 후 삭제";

export type AccountSettings = {
  displayName: string | null; // null = the Kakao nickname
  color: AvatarColor;
  notifyDone: boolean;
  notifyNotice: boolean;
  saveHistory: boolean;
  retention: Retention;
};

// One row of a conversation list: from this browser (guest) or from GET /chat (member)
export type ConversationItem = {
  id: string;
  title: string;
  updatedAt: number; // epoch ms
  questions: number | null; // null when the server list does not say
};
