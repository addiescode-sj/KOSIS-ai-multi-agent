"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import type { ReactNode } from "react";

import { getQueryClient } from "@/lib/query/client";

export function Providers({ children }: { children: ReactNode }) {
  // reducedMotion "user": motion components skip transform animations when the OS asks for less motion (T3-62)
  return (
    <QueryClientProvider client={getQueryClient()}>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </QueryClientProvider>
  );
}
