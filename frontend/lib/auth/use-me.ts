"use client";

import { useQueryClient, useSuspenseQuery } from "@tanstack/react-query";

import { queryKeys } from "@/lib/query/keys";
import type { AuthState } from "@/types/account";

import { fetchAuth } from "./me";

// The current user for every screen. The root layout prefetches it on the server (AuthHydration), so this
// suspends only until that answer streams in; refetched after login, logout and withdrawal. An error is read
// again on the next mount or focus.
export function useAuth(): AuthState {
  return useSuspenseQuery({
    queryKey: queryKeys.me,
    queryFn: fetchAuth,
    staleTime: (q) => (q.state.data?.status === "error" ? 0 : 5 * 60_000),
    retry: false,
  }).data;
}

export function useRefreshAuth() {
  const client = useQueryClient();
  return () => Promise.all([client.invalidateQueries({ queryKey: queryKeys.me }), client.removeQueries({ queryKey: queryKeys.chats })]);
}
