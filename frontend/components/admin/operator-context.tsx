"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { Me } from "@/lib/admin/screens";

// The signed-in operator (GET /admin/api/me, read by the page on the server after the checks): name, the form token
// a save sends back, the code labels. Only an operator's page ever holds it.
const OperatorContext = createContext<Me | null>(null);

export function OperatorProvider({ me, children }: { me: Me; children: ReactNode }) {
  return <OperatorContext value={me}>{children}</OperatorContext>;
}

export function useOperator(): Me {
  const me = useContext(OperatorContext);
  if (!me) throw new Error("useOperator outside an operator page");
  return me;
}

/** A stored code's Korean name (admin/app/labels.py), the code itself when unknown, `empty` when there is none. */
export function useLabel() {
  const { labels } = useOperator();
  return (kind: string, code: string | null | undefined, empty = "–") => (code == null || code === "" ? empty : (labels[kind]?.[code] ?? code));
}
