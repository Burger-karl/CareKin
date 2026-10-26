import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4 bg-background p-6 text-center">
      <h1 className="text-[24px] font-bold">We can&apos;t find that page</h1>
      <p className="max-w-sm text-muted-foreground">The link may be old, or the page has moved.</p>
      <Button asChild>
        <Link href="/">Go home</Link>
      </Button>
    </div>
  );
}
