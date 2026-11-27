"use client";

import { Menu, X } from "lucide-react";
import { Dialog } from "radix-ui";
import { useEffect, useState } from "react";

import { SearchForm } from "./search-form";
import { SidebarContent } from "./sidebar-content";

// Below 768px the sidebar is hidden (app-shell), so the header gets a ☰ button that slides the same SidebarContent
// in from the left (T3-62). Radix Dialog gives the focus trap, Esc / outside click, scroll lock and aria-expanded.
// It closes on any link inside (a same-page link or the full-load "새 대화" does not change the path), on a search,
// and when the window grows to 768px (the drawer is hidden there, but its scroll lock would stay). A dialog opened
// from inside (로그아웃) stacks on top, so the drawer stays open under it.
export function MobileNav({ query }: { query?: string }) {
  const [open, setOpen] = useState(false); // closed on the server and the first render: no hydration difference

  useEffect(() => {
    const wide = window.matchMedia("(min-width: 768px)");
    const close = (e: MediaQueryListEvent) => { if (e.matches) setOpen(false); };
    wide.addEventListener("change", close);
    return () => wide.removeEventListener("change", close);
  }, []);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger
        aria-label="메뉴 열기"
        className="-ml-1.5 flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-lg text-ink hover:bg-gray-100 focus-visible:ring-4 focus-visible:ring-brand-100 focus-visible:outline-none md:hidden"
      >
        <Menu size={22} />
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-gray-900/40 backdrop-blur-[2px] data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none md:hidden" />
        <Dialog.Content
          aria-describedby={undefined}
          onClick={(e) => { if ((e.target as HTMLElement).closest("a")) setOpen(false); }}
          className="fixed inset-y-0 left-0 z-50 flex h-dvh w-72 max-w-[85vw] flex-col bg-white shadow-2xl duration-200 data-[state=closed]:animate-out data-[state=closed]:slide-out-to-left data-[state=open]:animate-in data-[state=open]:slide-in-from-left motion-reduce:animate-none md:hidden"
        >
          <Dialog.Title className="sr-only">메뉴</Dialog.Title>
          <SidebarContent search={<SearchForm query={query} className="flex" onSearch={() => setOpen(false)} />} />
          <Dialog.Close aria-label="메뉴 닫기" className="absolute top-4 right-3 flex size-10 cursor-pointer items-center justify-center rounded-lg text-mute hover:bg-gray-100 focus-visible:ring-4 focus-visible:ring-brand-100 focus-visible:outline-none">
            <X size={20} />
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
