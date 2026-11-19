"use client";

import { ArrowUp, Square } from "lucide-react";
import { useState } from "react";

// Sticky question box under the conversation (prototype Composer). Enter sends, Shift+Enter breaks the line.
export function Composer({ busy, onSend, onStop, placeholder = "궁금한 통계를 평소 말로 입력하세요. 예: 2023년 합계출산율은?" }: {
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  function submit() {
    if (!text.trim() || busy) return;
    onSend(text.trim());
    setText("");
  }
  return (
    <div className="sticky bottom-0 bg-gradient-to-t from-canvas via-canvas to-transparent pt-6 pb-2">
      <form
        className="flex items-end gap-2 rounded-2xl border border-line bg-white p-2 pl-4 shadow-[0_8px_30px_rgba(17,24,39,0.06)] focus-within:border-brand-500"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={placeholder}
          aria-label="질문"
          className="min-h-10 flex-1 resize-none bg-transparent py-2.5 text-[15px] outline-none placeholder:text-mute"
        />
        {busy ? (
          <button type="button" onClick={onStop} aria-label="중단"
            className="flex size-10 cursor-pointer items-center justify-center rounded-xl border border-line bg-white text-sub hover:bg-gray-50">
            <Square size={16} />
          </button>
        ) : (
          <button type="submit" disabled={!text.trim()} aria-label="전송"
            className="flex size-10 cursor-pointer items-center justify-center rounded-xl bg-brand-600 text-white hover:bg-brand-700 disabled:cursor-default disabled:opacity-50">
            <ArrowUp size={18} />
          </button>
        )}
      </form>
    </div>
  );
}
