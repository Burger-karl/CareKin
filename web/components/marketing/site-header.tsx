"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Menu, Phone, X } from "lucide-react";
import { Logo } from "@/components/brand";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Home" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/about", label: "About" },
  { href: "/find-care", label: "Find a facility" },
  { href: "/faq", label: "FAQs" },
];

export function SiteHeader({ overDark = false }: { overDark?: boolean }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Over a dark hero and not yet scrolled: light text, glass nav.
  const dark = overDark && !scrolled && !open;

  return (
    <header
      className={cn(
        "sticky top-0 z-40 transition-colors",
        dark ? "bg-transparent" : scrolled || open ? "bg-[#FAF8F5]/90 shadow-sm backdrop-blur-md" : "bg-transparent",
      )}
    >
      <div className="mx-auto flex h-[72px] max-w-7xl items-center justify-between gap-4 px-4 md:px-6">
        <span className={cn("rounded-full transition-colors", dark && "bg-white/95 py-1.5 pl-2 pr-3.5")}>
          <Logo />
        </span>

        <nav
          aria-label="Main"
          className={cn(
            "hidden items-center gap-1 rounded-full p-1.5 ring-1 lg:flex",
            dark ? "bg-white/10 ring-white/15 backdrop-blur" : "bg-white/80 ring-line",
          )}
        >
          {NAV.map(({ href, label }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "rounded-full px-4 py-2 text-[15px] font-medium transition-colors",
                  active
                    ? dark
                      ? "bg-white text-ink"
                      : "bg-ink text-white"
                    : dark
                      ? "text-white/80 hover:bg-white/10 hover:text-white"
                      : "text-ink/75 hover:bg-mint-soft hover:text-ink",
                )}
              >
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-2">
          <a
            href="tel:112"
            className="hidden h-10 items-center gap-1.5 rounded-full bg-crisis-soft px-3.5 text-[14px] font-bold text-crisis-ink ring-1 ring-[#F3B8BB] hover:brightness-95 sm:inline-flex"
          >
            <Phone className="size-4" aria-hidden /> Call 112
          </a>
          <Link
            href="/login"
            className={cn(
              "hidden h-10 items-center rounded-full px-4 text-[15px] font-semibold md:inline-flex",
              dark ? "text-white hover:bg-white/10" : "text-ink hover:bg-white",
            )}
          >
            Sign in
          </Link>
          <Link
            href="/register"
            className={cn(
              "hidden h-10 items-center rounded-full px-5 text-[15px] font-semibold md:inline-flex",
              dark ? "bg-white text-ink hover:bg-mint-soft" : "bg-ink text-white hover:bg-[#0B3B36]",
            )}
          >
            Get started
          </Link>
          <button
            type="button"
            className="flex size-11 items-center justify-center rounded-full bg-white ring-1 ring-line lg:hidden"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
        </div>
      </div>

      {open && (
        <nav aria-label="Main" className="border-t border-line bg-[#FAF8F5] px-4 pb-6 pt-2 lg:hidden">
          <ul className="space-y-1">
            {NAV.map(({ href, label }) => (
              <li key={href}>
                <Link href={href} className="block rounded-xl px-3 py-3 text-[17px] font-semibold text-ink hover:bg-white">
                  {label}
                </Link>
              </li>
            ))}
          </ul>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <Link href="/login" className="flex h-12 items-center justify-center rounded-full bg-white font-semibold ring-1 ring-line">
              Sign in
            </Link>
            <Link href="/register" className="flex h-12 items-center justify-center rounded-full bg-ink font-semibold text-white">
              Get started
            </Link>
          </div>
          <a
            href="tel:112"
            className="mt-3 flex h-12 items-center justify-center gap-2 rounded-full bg-crisis font-bold text-white"
          >
            <Phone className="size-4" aria-hidden /> Emergency? Call 112
          </a>
        </nav>
      )}
    </header>
  );
}
