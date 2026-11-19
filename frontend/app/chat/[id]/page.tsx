import { Suspense } from "react";

import { SavedChat } from "@/components/chat/entries";

export default function Page({ params }: PageProps<"/chat/[id]">) {
  return (
    <Suspense>
      <SavedChat params={params} />
    </Suspense>
  );
}
