import { MessageSquareOff } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

// A saved conversation that cannot be shown (not found or not read), with a way out
export function MissingChat({ title, text }: { title: string; text: string }) {
  return (
    <Card className="mx-auto flex max-w-lg flex-col items-center p-8 text-center">
      <span className="flex size-14 items-center justify-center rounded-full bg-canvas text-mute"><MessageSquareOff size={26} /></span>
      <h2 className="mt-5 text-lg font-semibold">{title}</h2>
      <p className="mt-1.5 text-sm text-sub">{text}</p>
      <Button asChild className="mt-6"><Link href="/">새 대화 시작</Link></Button>
    </Card>
  );
}
