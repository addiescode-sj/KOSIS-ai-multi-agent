import { Button } from "@/components/ui/button";
import type { ChatList } from "@/lib/chat/use-chat-list";

// "이전 대화 더 보기" under a member list that has more pages on the server
export function LoadMore({ list }: { list: ChatList }) {
  return (
    <div className="flex flex-col items-center gap-2 pt-1">
      {list.moreFailed && <p role="alert" className="text-[13px] text-red-600">더 불러오지 못했습니다. 다시 시도해 주세요.</p>}
      <Button variant="secondary" disabled={list.loadingMore} onClick={list.loadMore}>
        {list.loadingMore ? "불러오는 중…" : "이전 대화 더 보기"}
      </Button>
    </div>
  );
}
