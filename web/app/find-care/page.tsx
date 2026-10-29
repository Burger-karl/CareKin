import type { Metadata } from "next";
import Link from "next/link";
import { Logo } from "@/components/brand";
import { PublicFinder } from "@/components/facilities/public-finder";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = { title: "Find an SCD facility" };

export default function FindCare() {
  return (
    <div className="min-h-dvh bg-background">
      <header className="border-b-[1.5px] border-line bg-white">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4">
          <Logo />
          <nav aria-label="Account" className="flex items-center gap-2">
            <Button asChild variant="ghost" size="sm">
              <Link href="/login">Sign in</Link>
            </Button>
            <Button asChild size="sm">
              <Link href="/register">Get started</Link>
            </Button>
          </nav>
        </div>
      </header>
      <main className="px-4 py-8 md:py-12">
        <PublicFinder />
      </main>
    </div>
  );
}
