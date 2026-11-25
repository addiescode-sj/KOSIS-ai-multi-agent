"use client";

import { useMutation } from "@tanstack/react-query";

import { notifySettings } from "@/lib/account/settings";

import { logout, withdraw, type ActionResult } from "./account-actions";
import { useRefreshAuth } from "./use-me";

// The result is a value: read `data` ({ok: false, message} on failure). On success the login is read again, the
// member's cached conversations dropped, then `onDone` runs (here, not in mutate(): the screen may already show a
// guest and have unmounted the dialog by then).

export function useLogout(onDone: () => void) {
  const refresh = useRefreshAuth();
  return useMutation<ActionResult>({
    mutationFn: logout,
    onSuccess: async (r) => {
      if (!r.ok) return;
      await refresh();
      onDone();
    },
  });
}

export function useWithdraw(onDone: () => void) {
  const refresh = useRefreshAuth();
  return useMutation<ActionResult>({
    mutationFn: withdraw,
    onSuccess: async (r) => {
      if (!r.ok) return;
      notifySettings.reset(); // this browser's switches belonged to the account
      await refresh();
      onDone();
    },
  });
}
