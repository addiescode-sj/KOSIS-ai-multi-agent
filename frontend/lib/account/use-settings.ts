"use client";

import { useSyncExternalStore } from "react";

import type { AccountSettings } from "@/types/account";

import { accountSettings, DEFAULT_SETTINGS } from "./settings";

export function useAccountSettings(): AccountSettings {
  return useSyncExternalStore(accountSettings.subscribe, accountSettings.get, () => DEFAULT_SETTINGS);
}
