import { MoreHorizontal } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "cn";

import { Card } from "@/components/ui/card";

// A titled admin card (mockup Card + CardHeader): title, optional sub line and action (a "more" button by default)
export function Panel({ title, sub, action, className, children }: {
  title: string;
  sub?: string;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Card className={cn("p-6", className)}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
          {sub && <p className="mt-0.5 text-[13px] text-sub">{sub}</p>}
        </div>
        {action ?? (
          <button type="button" className="rounded-md p-1 text-mute hover:bg-gray-100" aria-label={`${title} 더보기`}>
            <MoreHorizontal size={18} />
          </button>
        )}
      </div>
      {children}
    </Card>
  );
}
