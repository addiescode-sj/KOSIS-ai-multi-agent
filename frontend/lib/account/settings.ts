import { z } from "zod";

import type { Me } from "../auth/schemas.ts";
import { createPersisted, persisted, type KV, type PersistedStore } from "../storage/persisted.ts";
import type { AvatarColor, NotifySettings } from "../../types/account.ts";

// The settings screen. Name, color and history are on the account (GET / PATCH /auth/me); the notification switches
// have nothing behind them yet and are kept in this browser (localStorage through lib/storage; the screen says so).

export const SETTINGS_KEY = "kosis.notify.v1";
export const NAME_MAX = 20;
export const AVATAR_COLORS: Record<AvatarColor, string> = {
  blue: "bg-brand-100 text-brand-700",
  green: "bg-emerald-100 text-emerald-700",
  amber: "bg-amber-100 text-amber-800",
  rose: "bg-rose-100 text-rose-700",
  slate: "bg-slate-200 text-slate-700",
};
export const RETENTION_DAYS: Me["retention_days"][] = [90, 30];
// off until the member turns it on: a news mail needs consent
export const DEFAULT_NOTIFY: NotifySettings = { notifyDone: true, notifyNotice: false };

// Each switch on its own: an unknown or broken one reads as its default, the others stay
export const NotifySchema = z.looseObject({
  notifyDone: z.boolean().catch(DEFAULT_NOTIFY.notifyDone).default(DEFAULT_NOTIFY.notifyDone),
  notifyNotice: z.boolean().catch(DEFAULT_NOTIFY.notifyNotice).default(DEFAULT_NOTIFY.notifyNotice),
}).transform((v): NotifySettings => ({ notifyDone: v.notifyDone, notifyNotice: v.notifyNotice }));

export function createNotifyStore(storage: () => KV | null) {
  return notifyOn(createPersisted<NotifySettings>(SETTINGS_KEY, NotifySchema, { storage, fallback: DEFAULT_NOTIFY }));
}

function notifyOn(store: PersistedStore<NotifySettings>) {
  return {
    get: store.get,
    update(patch: Partial<NotifySettings>) {
      store.set((prev) => ({ ...prev, ...patch }));
    },
    reset: store.reset,
    subscribe: store.subscribe,
  };
}

// The name shown in the app: the chosen display name, else the Kakao nickname
export function nameOf(me: Pick<Me, "display_name" | "nickname">): string {
  return me.display_name?.trim() || me.nickname;
}

// A display name the form may save: trimmed, 1..NAME_MAX characters
export function validName(name: string): boolean {
  const n = [...name.trim()].length;
  return n > 0 && n <= NAME_MAX;
}

// This browser's switches (localStorage: they are not per tab), shared with usePersisted readers of the same key
export const notifySettings = notifyOn(persisted<NotifySettings>(SETTINGS_KEY, NotifySchema, { area: "local", fallback: DEFAULT_NOTIFY }));
