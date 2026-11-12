"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { CONTACT, CONTACT_MAILTO } from "@/lib/contact";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4 bg-background p-6 text-center">
      <h1 className="text-[24px] font-bold">Something went wrong</h1>
      <p className="max-w-sm text-muted-foreground">
        We hit a problem loading this page. If you are in a crisis, call 112 now.
      </p>
      <Button onClick={reset}>Try again</Button>
      <p className="text-meta text-muted-foreground">
        Still stuck? Email{" "}
        <a href={CONTACT_MAILTO} className="font-semibold underline">
          {CONTACT.email}
        </a>
      </p>
    </div>
  );
}
