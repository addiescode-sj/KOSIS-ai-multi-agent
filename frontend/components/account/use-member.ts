"use client";

import { nameOf } from "@/lib/account/settings";
import { useAccountSettings } from "@/lib/account/use-settings";
import { useAuth } from "@/lib/auth/use-me";

// The logged-in member as the screens show them (display name and color from the settings), or null
export function useMember() {
  const auth = useAuth();
  const settings = useAccountSettings();
  if (auth.status !== "member") return null;
  return { me: auth.me, name: nameOf(settings, auth.me.nickname), color: settings.color, settings };
}
