"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { cn } from "cn";

// The 대화·통계표 search box: goes to /search?q=… (the header's on wide screens, the mobile drawer's below 768px).
export function SearchForm({ query, className, onSearch }: { query?: string; className?: string; onSearch?: () => void }) {
  const router = useRouter();
  return (
    <form
      role="search"
      className={cn("h-10 items-center gap-2 rounded-lg border border-line px-3 text-sm text-mute focus-within:border-brand-500", className)}
      onSubmit={(e) => {
        e.preventDefault();
        const q = new FormData(e.currentTarget).get("q")?.toString().trim();
        if (!q) return;
        onSearch?.();
        router.push(`/search?q=${encodeURIComponent(q)}`);
      }}
    >
      <Search size={16} />
      <input
        name="q"
        key={query}
        defaultValue={query}
        placeholder="대화·통계표 검색"
        aria-label="대화·통계표 검색"
        className="w-full bg-transparent text-ink outline-none placeholder:text-mute"
      />
    </form>
  );
}
