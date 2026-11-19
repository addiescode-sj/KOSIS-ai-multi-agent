"use client";

import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { matchConversations } from "@/lib/chat/conversations";
import { useConversations } from "@/lib/chat/use-conversations";

import { LoadedResults } from "./loaded-results";
import { ResultsView } from "./results-view";
import { SearchForm } from "./search-form";

// `/search?q=`: this browser's conversations that mention the words, and the KOSIS tables phase 2 search finds.
// The shell and the conversations render at once; the tables stream in under the Suspense boundary.
export function SearchResults({ q }: { q: string }) {
  const list = useConversations();
  const convs = q ? matchConversations(list, q) : [];
  return (
    <AppShell title="검색 결과" sub={q ? `‘${q}’ 검색 결과` : "대화와 통계표를 검색합니다"} query={q}>
      <div className="mx-auto max-w-5xl space-y-6">
        <SearchForm q={q} />
        {q && (
          <Suspense key={q} fallback={<ResultsView q={q} convs={convs} tables={undefined} />}>
            <LoadedResults q={q} convs={convs} />
          </Suspense>
        )}
      </div>
    </AppShell>
  );
}
