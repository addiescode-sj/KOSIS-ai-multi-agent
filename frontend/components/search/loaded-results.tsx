"use client";

import { useSuspenseQuery } from "@tanstack/react-query";

import { tablesQuery } from "@/lib/search/tables";
import type { Conversation } from "@/types/conversation";

import { ResultsView } from "./results-view";

// Suspends until the table search for q is in: the promise the server prefetch started, or the browser's own
// request on a client-side search. A cached query renders at once.
export function LoadedResults({ q, convs }: { q: string; convs: Conversation[] }) {
  const { data } = useSuspenseQuery(tablesQuery(q));
  return <ResultsView q={q} convs={convs} tables={data} />;
}
