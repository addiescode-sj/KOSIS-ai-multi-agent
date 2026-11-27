import { dehydrate, HydrationBoundary } from "@tanstack/react-query";
import { connection } from "next/server";
import { Suspense } from "react";

import { TraceScreen } from "@/components/admin/trace-screen";
import { prefetchTrace } from "@/lib/admin/read.server";
import { SAMPLE_TRACE_ID } from "@/lib/admin/sample";
import { makeQueryClient } from "@/lib/query/client";

// One request's trace (?id=, the sample request by default), prefetched on the server like the dashboard
export default function Page({ searchParams }: PageProps<"/preview/admin/jaeger">) {
  return (
    <Suspense>
      {searchParams.then(({ id }) => <TraceData requestId={typeof id === "string" && id ? id : SAMPLE_TRACE_ID} />)}
    </Suspense>
  );
}

async function TraceData({ requestId }: { requestId: string }) {
  await connection();
  const client = makeQueryClient();
  prefetchTrace(client, requestId);
  return (
    <HydrationBoundary state={dehydrate(client)}>
      <Suspense>
        <TraceScreen requestId={requestId} />
      </Suspense>
    </HydrationBoundary>
  );
}
