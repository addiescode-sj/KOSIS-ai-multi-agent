import type { ReactNode } from "react";

import { requireOperator } from "@/lib/admin/operator.server";

import { OperatorProvider } from "./operator-context";

// Every /admin page renders inside this: nothing of the screen is drawn until the operator app says this is an
// operator (anyone else: 404). Server component; its read waits behind the root layout's <Suspense>.
export async function AdminGate({ children }: { children: ReactNode }) {
  const me = await requireOperator();
  return <OperatorProvider me={me}>{children}</OperatorProvider>;
}
