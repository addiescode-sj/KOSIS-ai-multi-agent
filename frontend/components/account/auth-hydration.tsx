import { dehydrate, HydrationBoundary } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { authOnServer } from "@/lib/auth/me.server";
import { loginCookie } from "@/lib/backend/server-read";
import { makeQueryClient } from "@/lib/query/client";
import { queryKeys } from "@/lib/query/keys";

// Root layout: reads the caller's login on the server and streams it to useAuth (useSuspenseQuery). Not awaited:
// the pending query goes down as a promise, so the page starts streaming before the backend answers.
export async function AuthHydration({ children }: { children: ReactNode }) {
  const cookie = await loginCookie();
  const client = makeQueryClient();
  void client.prefetchQuery({ queryKey: queryKeys.me, queryFn: () => authOnServer(cookie) });
  return <HydrationBoundary state={dehydrate(client)}>{children}</HydrationBoundary>;
}
