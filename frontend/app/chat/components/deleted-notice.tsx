import { CircleCheck } from "lucide-react";

// Shown on the /chat list after a conversation was deleted
export function DeletedNotice() {
  return (
    <div role="status" className="flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
      <CircleCheck size={18} />대화를 삭제했습니다.
    </div>
  );
}
