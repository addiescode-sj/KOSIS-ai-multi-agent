import { Suspense } from "react";

import { AdminGate } from "@/components/admin/admin-gate";
import { FeedbackScreen } from "@/components/admin/feedback-screen";
import { addressQuery, INBOX_KEYS } from "@/lib/admin/address";

// The thumbs-down inbox: filter in the address, a review form per row
export default function Page({ searchParams }: PageProps<"/admin/feedback">) {
  return (
    <AdminGate>
      <Suspense>
        {searchParams.then((p) => {
          const query = addressQuery(p, INBOX_KEYS);
          return <FeedbackScreen key={query} query={query} />;
        })}
      </Suspense>
    </AdminGate>
  );
}
