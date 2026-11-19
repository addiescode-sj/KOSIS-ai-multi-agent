"use client";

import { Search as SearchIcon, X } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";

// The big search box; a new query is a new `/search?q=` URL (the server prefetches its tables).
export function SearchForm({ q }: { q: string }) {
  const router = useRouter();
  const submit = (v: string) => router.push(v ? `/search?q=${encodeURIComponent(v)}` : "/search");
  return (
    <form
      role="search"
      onSubmit={(e) => { e.preventDefault(); submit(new FormData(e.currentTarget).get("q")?.toString().trim() ?? ""); }}
      className="flex items-center gap-3 rounded-2xl border border-line bg-white px-4 py-2 shadow-[0_8px_30px_rgba(17,24,39,0.05)] focus-within:border-brand-500"
    >
      <SearchIcon size={20} className="text-mute" />
      <input name="q" key={q} defaultValue={q} aria-label="검색어" className="h-11 flex-1 text-[16px] outline-none" placeholder="대화나 통계표를 검색하세요" />
      {q && <button type="button" onClick={() => submit("")} className="cursor-pointer rounded-md p-1 text-mute hover:bg-gray-100" aria-label="지우기"><X size={16} /></button>}
      <Button type="submit">검색</Button>
    </form>
  );
}
