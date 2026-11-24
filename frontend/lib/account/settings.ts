import type { AccountSettings, AvatarColor, Retention } from "../../types/account.ts";

// Profile, notification and history settings of the settings screen. The contract has no API for them yet
// (docs/service/api.md 6장), so they are kept in this browser; the screen says so. Retention stops at 90 days,
// the contract's limit for member conversations.

type KV = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export const SETTINGS_KEY = "kosis.account.v1";
export const NAME_MAX = 20;
export const AVATAR_COLORS: Record<AvatarColor, string> = {
  blue: "bg-brand-100 text-brand-700",
  green: "bg-emerald-100 text-emerald-700",
  amber: "bg-amber-100 text-amber-800",
  rose: "bg-rose-100 text-rose-700",
  slate: "bg-slate-200 text-slate-700",
};
export const RETENTIONS: Retention[] = ["90일 후 삭제", "30일 후 삭제"];
export const DEFAULT_SETTINGS: AccountSettings = {
  displayName: null, color: "blue", notifyDone: true, notifyNotice: false, saveHistory: true, retention: "90일 후 삭제",
};

export function createSettingsStore(storage: () => KV | null) {
  const listeners = new Set<() => void>();
  let cacheRaw: string | null | undefined;
  let cache = DEFAULT_SETTINGS;

  function read(): AccountSettings {
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
    update(patch: Partial<AccountSettings>) {
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

function parse(raw: string | null): AccountSettings {
  if (!raw) return DEFAULT_SETTINGS;
  try {
    const v = { ...DEFAULT_SETTINGS, ...JSON.parse(raw) } as AccountSettings;
    if (!(v.color in AVATAR_COLORS)) v.color = DEFAULT_SETTINGS.color;
    if (!RETENTIONS.includes(v.retention)) v.retention = DEFAULT_SETTINGS.retention;
    return v;
  } catch {
    return DEFAULT_SETTINGS;
  }
}

// The name shown in the app: the chosen display name, else the Kakao nickname
export function nameOf(settings: AccountSettings, nickname: string): string {
  return settings.displayName?.trim() || nickname;
}

// A display name the form may save: trimmed, 1..NAME_MAX characters
export function validName(name: string): boolean {
  const t = name.trim();
  return t.length > 0 && t.length <= NAME_MAX;
}

export const accountSettings = createSettingsStore(() => (typeof window === "undefined" ? null : window.localStorage));
