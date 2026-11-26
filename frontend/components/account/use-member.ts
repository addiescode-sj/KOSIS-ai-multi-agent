"use client";

import { nameOf } from "@/lib/account/settings";
import { useAuth } from "@/lib/auth/use-me";

// The logged-in member as the screens show them (display name and color from the account), or null
export function useMember() {
  const auth = useAuth();
  if (auth.status !== "member") return null;
  return { me: auth.me, name: nameOf(auth.me), color: auth.me.color };
}
