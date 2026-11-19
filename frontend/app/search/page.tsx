import { defaultShouldDehydrateQuery, dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import { connection } from "next/server";
import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { SearchResults } from "@/components/search/search-results";
import { searchTables } from "@/lib/search/search-tables.server";
import { tablesQuery } from "@/lib/search/tables";

// Hybrid: the shell is prerendered (fallback below); per request the server starts the table search without
// waiting for it and hands the pending query to the browser, which picks it up with useSuspenseQuery.
export default function Page({ searchParams }: PageProps<"/search">) {
  return (
    <Suspense fallback={<AppShell title="검색 결과" sub="대화와 통계표를 검색합니다"><div /></AppShell>}>
      {searchParams.then(({ q }) => <SearchData q={(typeof q === "string" ? q : "").trim()} />)}
    </Suspense>
  );
}

async function SearchData({ q }: { q: string }) {
  // QueryClient and dehydrate() read the current time: request time only, never in the prerendered shell
  await connection();
  const queryClient = new QueryClient();
  if (q) void queryClient.prefetchQuery({ ...tablesQuery(q), queryFn: () => searchTables(q) }); // not awaited
  return (
    <HydrationBoundary
      state={dehydrate(queryClient, {
        shouldDehydrateQuery: (query) => defaultShouldDehydrateQuery(query) || query.state.status === "pending",
      })}
    >
      <SearchResults q={q} />
    </HydrationBoundary>
  );
}
