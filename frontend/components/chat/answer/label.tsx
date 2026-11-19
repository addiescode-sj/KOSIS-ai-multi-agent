import type { ReactNode } from "react";

export function Label({ children }: { children: ReactNode }) {
  return <span className="text-xs font-medium text-mute">{children}</span>;
}
