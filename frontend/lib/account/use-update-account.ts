"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { authFrom } from "@/lib/auth/me";
import { ME_KEY } from "@/lib/auth/use-me";
import type { AccountUpdate } from "@/types/account";

// PATCH /auth/me; its answer is the new account, shown on every screen
export function useUpdateAccount() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (changes: AccountUpdate) => {
      const res = await fetch("/auth/me", {
        method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(changes),
      }).catch(() => null);
      const auth = res?.ok ? authFrom({ status: res.status, body: await res.json().catch(() => null) }) : null;
      if (auth?.status !== "member") throw new Error(`저장하지 못했습니다${res ? ` (${res.status})` : ""}. 잠시 뒤 다시 시도해 주세요.`);
      return auth;
    },
    onSuccess: (auth) => client.setQueryData(ME_KEY, auth),
  });
}
