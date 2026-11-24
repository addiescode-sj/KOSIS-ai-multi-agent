import { CircleAlert } from "lucide-react";

// /auth/me failed on the server side: say so; asking still works (as a guest-like turn without the account)
export function AuthNotice({ message }: { message: string }) {
  return (
    <p role="status" className="flex items-start gap-2 rounded-xl bg-amber-50 p-3 text-[13px] leading-relaxed text-amber-800">
      <CircleAlert size={15} className="mt-0.5 shrink-0" />
      {message} 질문은 계속할 수 있어요.
    </p>
  );
}
