import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "cn";

import type { Tone } from "@/types/answer";

const TONES: Record<Tone | "brand", string> = {
  brand: "bg-brand-50 text-brand-600",
  amber: "bg-amber-50 text-amber-600",
  red: "bg-red-50 text-red-500",
  gray: "bg-gray-100 text-sub",
};

export function Notice({ icon: Icon, tone, title, children }: { icon: LucideIcon; tone: Tone | "brand"; title: string; children: ReactNode }) {
  return (
    <div className="flex gap-4">
      <span className={cn("flex size-10 shrink-0 items-center justify-center rounded-full", TONES[tone])}><Icon size={19} /></span>
      <div className="min-w-0 flex-1">
        <h2 className="text-[17px] font-semibold">{title}</h2>
        <div className="mt-1.5 space-y-1 text-sm text-sub">{children}</div>
      </div>
    </div>
  );
}
