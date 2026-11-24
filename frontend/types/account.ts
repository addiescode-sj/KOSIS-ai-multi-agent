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

export type AvatarColor = Me["color"];

// The settings the account keeps (PATCH /auth/me)
export type AccountUpdate = Partial<Pick<Me, "display_name" | "color" | "save_history" | "retention_days">>;

// Notification switches: nothing sends them yet, so they stay in this browser
export type NotifySettings = { notifyDone: boolean; notifyNotice: boolean };

// One row of a conversation list: from this browser (guest) or from GET /chat (member)
export type ConversationItem = {
  id: string;
  title: string;
  updatedAt: number; // epoch ms
  questions: number;
};
