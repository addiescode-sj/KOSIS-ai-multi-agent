import type { Me } from "../auth/schemas.ts";
import type { AvatarColor, NotifySettings } from "../../types/account.ts";

// The settings screen. Name, color and history are on the account (GET / PATCH /auth/me); the notification switches
// have nothing behind them yet and are kept in this browser (the screen says so).

type KV = Pick<Storage, "getItem" | "setItem" | "removeItem">;

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

export function createNotifyStore(storage: () => KV | null) {
  const listeners = new Set<() => void>();
  let cacheRaw: string | null | undefined;
  let cache = DEFAULT_NOTIFY;

  function read(): NotifySettings {
    const raw = storage()?.getItem(SETTINGS_KEY) ?? null;
    if (raw !== cacheRaw) {
      cacheRaw = raw;
      cache = parse(raw);
    }
    return cache;
  }
  const notify = () => listeners.forEach((l) => l());

  return {
    get: read,
    update(patch: Partial<NotifySettings>) {
      storage()?.setItem(SETTINGS_KEY, JSON.stringify({ ...read(), ...patch }));
      notify();
    },
    reset() {
      storage()?.removeItem(SETTINGS_KEY);
      notify();
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

function parse(raw: string | null): NotifySettings {
  if (!raw) return DEFAULT_NOTIFY;
  try {
    return { ...DEFAULT_NOTIFY, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_NOTIFY;
  }
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

export const notifySettings = createNotifyStore(() => (typeof window === "undefined" ? null : window.localStorage));
