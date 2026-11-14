import Link from "next/link";
import { ArrowUpRight, Check } from "lucide-react";
import { cn } from "@/lib/utils";

/** Pill link with a round arrow badge on the right. */
export function PillLink({
  href,
  children,
  tone = "dark",
  className,
}: {
  href: string;
  children: React.ReactNode;
  tone?: "dark" | "light" | "outline" | "teal";
  className?: string;
}) {
  const tones = {
    dark: "bg-ink text-white hover:bg-[#0B3B36]",
    teal: "bg-primary text-white hover:bg-teal-dark",
    light: "bg-white text-ink hover:bg-mint-soft",
    outline: "bg-transparent text-ink ring-1 ring-ink/20 hover:bg-white",
  };
  const badge = {
    dark: "bg-white text-ink",
    teal: "bg-white text-teal",
    light: "bg-ink text-white",
    outline: "bg-ink text-white",
  };
  return (
    <Link
      href={href}
      className={cn(
        "group inline-flex h-14 items-center gap-3 rounded-full py-2 pl-6 pr-2 text-[16px] font-semibold transition-colors",
        tones[tone],
        className,
      )}
    >
      {children}
      <span
        className={cn(
          "flex size-10 items-center justify-center rounded-full transition-transform duration-300 group-hover:rotate-45",
          badge[tone],
        )}
        aria-hidden
      >
        <ArrowUpRight className="size-5" />
      </span>
    </Link>
  );
}

/** Small label above a heading. */
export function Eyebrow({ children, light = false }: { children: React.ReactNode; light?: boolean }) {
  return (
    <p
      className={cn(
        "inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[13px] font-semibold uppercase tracking-wider",
        light ? "bg-white/10 text-white ring-1 ring-white/20" : "bg-white text-teal ring-1 ring-line",
      )}
    >
      <span aria-hidden className={cn("size-1.5 rounded-full", light ? "bg-[#F7B3A1]" : "bg-[#E9785F]")} /> {children}
    </p>
  );
}

/** Big two-tone section heading. `muted` renders in a softer shade after the main text. */
export function SectionTitle({
  id,
  children,
  muted,
  light = false,
  className,
}: {
  id?: string;
  children: React.ReactNode;
  muted?: React.ReactNode;
  light?: boolean;
  className?: string;
}) {
  return (
    <h2
      id={id}
      className={cn(
        "mt-4 text-[34px] font-bold leading-[1.08] tracking-tight md:text-[52px]",
        light ? "text-white" : "text-ink",
        className,
      )}
    >
      {children}
      {muted && <span className={light ? " text-white/55" : " text-ink/40"}> {muted}</span>}
    </h2>
  );
}

/** A tick list item used across the marketing pages. */
export function Tick({ children, light = false }: { children: React.ReactNode; light?: boolean }) {
  return (
    <li className="flex items-start gap-3 text-[16px] leading-snug">
      <span
        className={cn(
          "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full",
          light ? "bg-white/15 text-white" : "bg-mint text-teal",
        )}
        aria-hidden
      >
        <Check className="size-3.5" strokeWidth={3} />
      </span>
      <span className={light ? "text-white/90" : "text-foreground/85"}>{children}</span>
    </li>
  );
}
