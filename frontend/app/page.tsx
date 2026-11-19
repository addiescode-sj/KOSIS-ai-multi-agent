import { Suspense } from "react";

import { NewChat } from "@/components/chat/entries";

export default function Page() {
  return (
    <Suspense>
      <NewChat />
    </Suspense>
  );
}
