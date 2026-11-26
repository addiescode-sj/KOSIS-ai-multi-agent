import { defaultShouldDehydrateQuery, isServer, QueryClient } from "@tanstack/react-query";

import { queryKeys } from "./keys.ts";

// Server components prefetch without awaiting: a pending query is sent to the browser as its promise, and the
// client component that reads it with useSuspenseQuery streams in when it resolves.
export function makeQueryClient() {
  const client = new QueryClient({
    defaultOptions: {
      dehydrate: { shouldDehydrateQuery: (q) => defaultShouldDehydrateQuery(q) || q.state.status === "pending" },
    },
  });
  // An opened member conversation is dropped a second after no screen shows it (the shortest time a suspending
  // query allows), so opening it again reads the turns asked since. Set per key, so a query the server hydrates
  // gets it too; the cache's own timer, which also lets a remount (React's dev double effects) attach first.
  client.setQueryDefaults(queryKeys.chatDetails, { gcTime: 1000 });
  return client;
}

let browserClient: QueryClient | undefined;

// A new client per server render (requests stay apart), one client for the life of the browser tab (the cache).
export function getQueryClient() {
  if (isServer) return makeQueryClient();
  browserClient ??= makeQueryClient();
  return browserClient;
}
