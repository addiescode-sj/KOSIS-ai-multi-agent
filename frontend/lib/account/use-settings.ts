"use client";

import { useSyncExternalStore } from "react";

import type { NotifySettings } from "@/types/account";

import { DEFAULT_NOTIFY, notifySettings } from "./settings";

export function useNotifySettings(): NotifySettings {
  return useSyncExternalStore(notifySettings.subscribe, notifySettings.get, () => DEFAULT_NOTIFY);
}
