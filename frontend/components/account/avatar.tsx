import { cn } from "cn";

import { AVATAR_COLORS } from "@/lib/account/settings";
import type { AvatarColor } from "@/types/account";

const SIZES = { sm: "size-9 text-sm", lg: "size-16 text-2xl" };

// The first letter of the shown name on the chosen color
export function Avatar({ name, color, size = "sm" }: { name: string; color: AvatarColor; size?: keyof typeof SIZES }) {
  return (
    <span aria-hidden className={cn("flex shrink-0 items-center justify-center rounded-full font-semibold", SIZES[size], AVATAR_COLORS[color])}>
      {name.trim().slice(0, 1) || "?"}
    </span>
  );
}
