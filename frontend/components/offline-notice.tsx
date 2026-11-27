"use client";

import { WifiOff } from "lucide-react";

import { useOnline } from "@/lib/browser/use-online";
import { ANSWER_ERRORS } from "@/lib/chat/errors";

// One line on top of every customer screen while the browser is offline (T3-67 `offline`); it goes away by itself
// when the connection is back.
export function OfflineNotice() {
  const online = useOnline();
  if (online) return null;
  return (
    <div role="status" className="sticky top-0 z-30 flex items-center justify-center gap-2 bg-gray-900 px-4 py-2 text-[13px] text-white">
      <WifiOff size={15} aria-hidden />
      {ANSWER_ERRORS.offline.title}. 연결되면 자동으로 다시 쓸 수 있어요.
    </div>
  );
}
