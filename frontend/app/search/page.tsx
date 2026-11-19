import { Suspense } from "react";

import { SearchResults } from "@/components/search-results";

export default function Page() {
  return (
    <Suspense>
      <SearchResults />
    </Suspense>
  );
}
