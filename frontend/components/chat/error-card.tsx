"use client";

import { CircleAlert, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { type ErrorView, supportId } from "@/lib/chat/errors";

// The card of a turn that ended without an answer (T3-68): the error code's title and sentence, a small support
// number (the first 8 characters of the request_id) to quote when asking for help, and "다시 시도".
export function ErrorCard({ view, requestId, onRetry }: {
  view: ErrorView;
  requestId: string | null;
  onRetry?: () => void;
}) {
  const support = supportId(requestId);
  return (
    <Card role="alert" className="flex items-start gap-4 p-6">
      <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-red-50 text-red-500"><CircleAlert size={19} /></span>
      <div className="min-w-0 flex-1">
        <h2 className="text-[17px] font-semibold">{view.title}</h2>
        <p className="mt-1.5 text-sm break-words text-sub">{view.text}</p>
        {support && <p className="mt-2 text-xs text-mute tabular-nums">문의 번호 {support}</p>}
      </div>
      {onRetry && <Button onClick={onRetry}><RotateCw />다시 시도</Button>}
    </Card>
  );
}
