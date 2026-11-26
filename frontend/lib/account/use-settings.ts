"use client";

import { usePersisted } from "@/lib/storage/use-persisted";
import type { NotifySettings } from "@/types/account";

import { DEFAULT_NOTIFY, NotifySchema, SETTINGS_KEY } from "./settings";

// The same store as `notifySettings` (one per key): a switch changed there shows here, in other tabs too.
export function useNotifySettings(): NotifySettings {
  return usePersisted(SETTINGS_KEY, NotifySchema, { area: "local", fallback: DEFAULT_NOTIFY })[0];
}
