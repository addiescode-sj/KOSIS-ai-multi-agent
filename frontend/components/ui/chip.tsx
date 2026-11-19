import type { ComponentProps } from "react";
import { cn } from "cn";

export function Chip({ active, className, ...props }: ComponentProps<"button"> & { active?: boolean }) {
  return (
    <button
      type="button"
      className={cn(
        "h-8 cursor-pointer rounded-full border px-3.5 text-[13px] font-medium transition-colors disabled:cursor-default",
        active ? "border-brand-600 bg-brand-600 text-white" : "border-line bg-white text-sub hover:border-brand-300 hover:text-brand-600",
        className,
      )}
      {...props}
    />
  );
}
