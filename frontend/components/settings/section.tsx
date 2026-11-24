import type { ReactNode } from "react";
import { cn } from "cn";

import { Card } from "@/components/ui/card";

export function Section({ id, title, desc, danger, children }: {
  id: string;
  title: string;
  desc?: ReactNode;
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <Card id={id} aria-labelledby={`${id}-title`} role="region" className={cn("p-6", danger && "border-red-100")}>
      <h2 id={`${id}-title`} className={cn("text-base font-semibold", danger && "text-red-600")}>{title}</h2>
      {desc && <p className="mt-1 text-[13px] text-sub">{desc}</p>}
      <div className="mt-5">{children}</div>
    </Card>
  );
}
