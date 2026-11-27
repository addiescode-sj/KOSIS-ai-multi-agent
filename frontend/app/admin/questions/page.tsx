import { Suspense } from "react";

import { AdminGate } from "@/components/admin/admin-gate";
import { QuestionsScreen } from "@/components/admin/questions-screen";
import { addressQuery, QUESTION_KEYS } from "@/lib/admin/address";

// Question review: the list of the period, filter in the address
export default function Page({ searchParams }: PageProps<"/admin/questions">) {
  return (
    <AdminGate>
      <Suspense>
        {searchParams.then((p) => {
          const query = addressQuery(p, QUESTION_KEYS);
          return <QuestionsScreen key={query} query={query} />;
        })}
      </Suspense>
    </AdminGate>
  );
}
