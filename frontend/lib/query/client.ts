import { defaultShouldDehydrateQuery, isServer, QueryClient } from "@tanstack/react-query";

// Server components prefetch without awaiting: a pending query is sent to the browser as its promise, and the
// client component that reads it with useSuspenseQuery streams in when it resolves.
export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      dehydrate: { shouldDehydrateQuery: (q) => defaultShouldDehydrateQuery(q) || q.state.status === "pending" },
    },
  });
}

let browserClient: QueryClient | undefined;

// A new client per server render (requests stay apart), one client for the life of the browser tab (the cache).
export function getQueryClient() {
  if (isServer) return makeQueryClient();
  browserClient ??= makeQueryClient();
  return browserClient;
}
