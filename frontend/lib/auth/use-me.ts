"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { AuthState } from "@/types/account";

import { fetchAuth } from "./me";

export const ME_KEY = ["auth", "me"] as const;

// The current user for every screen. One request per tab; refetched after login, logout and withdrawal.
export function useAuth(): AuthState {
  const { data } = useQuery({ queryKey: ME_KEY, queryFn: fetchAuth, staleTime: 5 * 60_000, retry: false });
  return data ?? { status: "loading" };
}

export function useRefreshAuth() {
  const client = useQueryClient();
  return () => Promise.all([client.invalidateQueries({ queryKey: ME_KEY }), client.removeQueries({ queryKey: ["chats"] })]);
}
