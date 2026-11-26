"use client";

import { ThumbsDown, ThumbsUp, X } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import type { TurnFeedback } from "@/lib/chat/schemas";
import {
  CATEGORY_LABELS, FEEDBACK_CATEGORIES, type FeedbackCategory, type FeedbackInput, feedbackProblem, type FeedbackValue,
  REASON_MAX,
} from "@/lib/feedback/feedback";
import { useSendFeedback } from "@/lib/feedback/use-feedback";

// 👍/👎 under one answer (api.md 3장). A thumb sends the value at once (category null), so closing the panel without
// a choice still keeps it; a choice (+ reason, required for 기타) sent from the panel replaces it. What the screen
// shows is the request in flight, else the last one the server took: a failed send falls back by itself.
export function FeedbackBar({ requestId, initial, onSaved }: {
  requestId: string;
  initial: TurnFeedback | null;
  onSaved?: (feedback: TurnFeedback) => void;
}) {
  const mutation = useSendFeedback();
  const [saved, setSaved] = useState<TurnFeedback | null>(initial);
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<FeedbackCategory | null>(null);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const shown = mutation.isPending && mutation.variables ? mutation.variables.value : saved?.value ?? null;
  const failed = mutation.data && !mutation.data.ok ? mutation.data.message : null;

  function send(input: FeedbackInput, close: boolean) {
    mutation.mutate(input, {
      onSuccess: (result) => {
        if (!result.ok) return;
        const feedback = { value: input.value, category: input.category };
        setSaved(feedback);
        onSaved?.(feedback);
        if (close) setOpen(false);
      },
    });
  }

  function pick(value: FeedbackValue) {
    mutation.reset();
    setProblem(null);
    setOpen(true);
    if (value === shown) return; // the same thumb again only reopens the choices
    setCategory(null);
    send({ requestId, value, category: null }, false);
  }

  function submit() {
    if (!shown) return;
    const input = { requestId, value: shown, category, reason };
    const bad = feedbackProblem(input);
    if (bad) return setProblem(bad);
    send(input, true);
  }

  const choices = shown ? FEEDBACK_CATEGORIES[shown] : [];
  return (
    <div className="space-y-3 border-t border-line pt-4" data-testid="feedback">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] text-sub">이 답이 도움이 되었나요?</span>
        <Thumb value="up" active={shown === "up"} onClick={() => pick("up")} />
        <Thumb value="down" active={shown === "down"} onClick={() => pick("down")} />
        {saved?.category && !open && (
          <span className="text-xs text-mute">보낸 의견 · {CATEGORY_LABELS[saved.category as FeedbackCategory] ?? saved.category}</span>
        )}
      </div>
      {open && shown && (
        <div className="space-y-3 rounded-xl bg-canvas p-4" role="group" aria-label="피드백 선택지">
          <div className="flex items-center justify-between">
            <p className="text-[13px] font-medium">{shown === "up" ? "어떤 점이 좋았나요?" : "어떤 점이 아쉬웠나요?"} <span className="font-normal text-mute">(선택)</span></p>
            <button type="button" aria-label="피드백 닫기" onClick={() => { mutation.reset(); setOpen(false); }}
              className="cursor-pointer rounded-md p-1 text-mute hover:bg-gray-100 hover:text-ink">
              <X size={15} />
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {choices.map((c) => (
              <Chip key={c} active={category === c} aria-pressed={category === c}
                onClick={() => { mutation.reset(); setProblem(null); setCategory(category === c ? null : c); }}>
                {CATEGORY_LABELS[c]}
              </Chip>
            ))}
          </div>
          <div>
            <textarea
              rows={2}
              value={reason}
              maxLength={REASON_MAX}
              onChange={(e) => { mutation.reset(); setProblem(null); setReason(e.target.value); }}
              placeholder={category === "other" ? "이유를 적어 주세요 (필수)" : "이유를 적어 주세요 (선택)"}
              aria-label="피드백 이유"
              className="w-full resize-none rounded-lg border border-line bg-white px-3 py-2 text-[13px] outline-none focus:border-brand-500"
            />
            <p className="text-right text-xs text-mute tabular-nums">{[...reason].length}/{REASON_MAX}</p>
          </div>
          <div className="flex items-center justify-end gap-2">
            <Button size="sm" onClick={submit} disabled={mutation.isPending || (category === null && !reason.trim())}>보내기</Button>
          </div>
        </div>
      )}
      {(problem ?? failed) && <p role="alert" className="text-[13px] text-red-600">{problem ?? failed}</p>}
      {mutation.data?.ok && !open && <p className="sr-only" role="status">피드백을 보냈습니다</p>}
    </div>
  );
}

function Thumb({ value, active, onClick }: { value: FeedbackValue; active: boolean; onClick: () => void }) {
  const Icon = value === "up" ? ThumbsUp : ThumbsDown;
  const label = value === "up" ? "도움이 됐어요" : "아쉬워요";
  return (
    <button type="button" onClick={onClick} aria-label={label} aria-pressed={active} title={label}
      className={cn(
        "flex size-8 cursor-pointer items-center justify-center rounded-lg border transition-colors",
        active ? "border-brand-600 bg-brand-50 text-brand-600" : "border-line bg-white text-sub hover:border-brand-300 hover:text-brand-600",
      )}>
      <Icon size={15} />
    </button>
  );
}
