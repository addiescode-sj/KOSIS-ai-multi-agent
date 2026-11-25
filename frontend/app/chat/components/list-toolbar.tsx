import { Search } from "lucide-react";
import type { ChangeEvent } from "react";

import { Button } from "@/components/ui/button";
import { SEARCH_PLACEHOLDER } from "@/lib/chat/list-view";

// Search box and "새 대화" above the /chat list
export function ListToolbar({ query, member, onQuery }: { query: string; member: boolean; onQuery: (q: string) => void }) {
  function handleChange(e: ChangeEvent<HTMLInputElement>) {
    onQuery(e.target.value);
  }

  return (
    <div className="flex items-center gap-3">
      <label className="flex h-10 flex-1 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm focus-within:border-brand-500">
        <Search size={16} className="text-mute" />
        <input
          value={query}
          onChange={handleChange}
          className="w-full outline-none"
          placeholder={member ? SEARCH_PLACEHOLDER.member : SEARCH_PLACEHOLDER.guest}
        />
      </label>
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- full load: see the "새 대화" link in app-shell */}
      <Button asChild><a href="/">새 대화</a></Button>
    </div>
  );
}
