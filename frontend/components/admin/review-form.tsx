"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { saveReview } from "@/lib/admin/queries";
import type { Person, Review } from "@/lib/admin/screens";
import { queryKeys } from "@/lib/query/keys";

import { useLabel, useOperator } from "./operator-context";

const FIELD = "h-10 w-full rounded-lg border border-line bg-white px-3 text-sm outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-100";
const NOTE_MAX = 200; // admin/app/models.NOTE_MAX

// The review of one thumbs-down answer: status, assignee, a short note (masked by the operator app), the Linear
// number. The operator app refuses a save when someone saved after this form was read (409): the screen says so
// and the operator reads it again. `onSaved` gets the stored review.
export function ReviewForm({ requestId, review, people, onSaved }: {
  requestId: string;
  review: Review | null;
  people: Person[];
  onSaved?: (review: Review) => void;
}) {
  const me = useOperator();
  const label = useLabel();
  const client = useQueryClient();
  const [form, setForm] = useState({
    status: review?.status ?? "new",
    assignee: review?.assignee_id ?? "",
    note: review?.note ?? "",
    issue_key: review?.issue_key ?? "",
  });
  const save = useMutation({
    mutationFn: () => saveReview(requestId, { ...form, seen: review?.updated_at ?? "" }, me.form_token),
    onSuccess: (res) => {
      if (!res.ok) return;
      void client.invalidateQueries({ queryKey: ["admin", "inbox"] });
      void client.invalidateQueries({ queryKey: queryKeys.adminHome });
      onSaved?.(res.review);
    },
  });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => {
    save.reset();
    setForm({ ...form, [k]: e.target.value });
  };
  const result = save.data;
  return (
    <form
      className="grid grid-cols-[10rem_10rem_1fr_9rem_auto] items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="text-[13px] text-sub">상태
        <select value={form.status} onChange={set("status")} className={cn(FIELD, "mt-1")}>
          {me.statuses.map((s) => <option key={s} value={s}>{label("status", s)}</option>)}
        </select>
      </label>
      <label className="text-[13px] text-sub">담당
        <select value={form.assignee} onChange={set("assignee")} className={cn(FIELD, "mt-1")}>
          <option value="">없음 (확인 중이면 나)</option>
          {people.map((p) => <option key={p.id} value={p.id}>{p.name ?? p.id.slice(0, 8)}</option>)}
        </select>
      </label>
      <label className="text-[13px] text-sub">메모 (개인정보는 가려서 저장, {NOTE_MAX}자)
        <input value={form.note} onChange={set("note")} maxLength={NOTE_MAX} className={cn(FIELD, "mt-1")} placeholder="문제 없음이면 필수" />
      </label>
      <label className="text-[13px] text-sub">Linear 번호
        <input value={form.issue_key} onChange={set("issue_key")} className={cn(FIELD, "mt-1")} placeholder="KOS-201" />
      </label>
      <Button type="submit" disabled={save.isPending}>{save.isPending ? "저장 중…" : "저장"}</Button>
      {result && (
        <p role={result.ok ? "status" : "alert"} className={cn("col-span-full text-sm", result.ok ? "text-emerald-700" : "text-red-600")}>
          {result.ok ? "저장했습니다." : result.message}
          {!result.ok && result.conflict && " 새로고침하면 다른 사람이 바꾼 내용을 볼 수 있습니다."}
        </p>
      )}
    </form>
  );
}
