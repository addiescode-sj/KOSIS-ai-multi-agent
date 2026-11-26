"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { MeUpdate, Me } from "@/lib/auth/schemas";
import { request } from "@/lib/backend/request";
import { queryKeys } from "@/lib/query/keys";
import type { AccountUpdate, AuthState } from "@/types/account";

// PATCH /auth/me (body checked with MeUpdate, answer with Me); its answer is the new account, shown on every screen.
// The screens read mutation.error.message, so a failed value is raised here, inside the mutation only.
export function useUpdateAccount() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (changes: AccountUpdate): Promise<AuthState> => {
      const res = await request("/auth/me", { method: "PATCH", body: changes, bodySchema: MeUpdate, responseSchema: Me });
      if (!res.ok) throw new Error(`저장하지 못했습니다${res.status ? ` (${res.status})` : ""}. 잠시 뒤 다시 시도해 주세요.`);
      return { status: "member", me: res.data };
    },
    onSuccess: (auth) => client.setQueryData(queryKeys.me, auth),
  });
}
