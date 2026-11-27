import { Suspense } from "react";

import { AdminGate } from "@/components/admin/admin-gate";
import { QuestionScreen } from "@/components/admin/question-screen";

// One question (opening it is recorded by the operator app)
export default function Page({ params }: PageProps<"/admin/questions/[id]">) {
  return (
    <AdminGate>
      <Suspense>
        {params.then(({ id }) => <QuestionScreen key={id} id={id} />)}
      </Suspense>
    </AdminGate>
  );
}
