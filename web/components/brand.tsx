import Image from "next/image";
import Link from "next/link";
import { cn } from "@/lib/utils";

// public/brand/*.png are cut from the source logo by scripts/make-brand-assets.mjs.
const MARK_RATIO = 256 / 284; // width / height
const WORD_RATIO = 520 / 119;

/** The heart mark: carer and patient forming a heart. */
export function LogoMark({ size = 36, className }: { size?: number; className?: string }) {
  return (
    <Image
      src="/brand/mark.png"
      alt=""
      aria-hidden
      width={Math.round(size * MARK_RATIO)}
      height={size}
      priority
      className={cn("shrink-0", className)}
      style={{ width: Math.round(size * MARK_RATIO), height: size }}
    />
  );
}

/** The "CareKin" lettering, exactly as in the logo. */
export function LogoWordmark({ height = 22, className }: { height?: number; className?: string }) {
  return (
    <Image
      src="/brand/wordmark.png"
      alt="CareKin"
      width={Math.round(height * WORD_RATIO)}
      height={height}
      priority
      className={cn("shrink-0", className)}
      style={{ width: Math.round(height * WORD_RATIO), height }}
    />
  );
}

/** Mark and lettering side by side, linking home. */
export function Logo({
  className,
  sub,
  href = "/",
  size = 36,
}: {
  className?: string;
  sub?: string;
  href?: string;
  /** Height of the mark in pixels; the lettering scales with it. */
  size?: number;
}) {
  return (
    <Link href={href} className={cn("inline-flex items-center gap-2", className)} aria-label="CareKin home">
      <LogoMark size={size} />
      <LogoWordmark height={Math.round(size * 0.62)} />
      {sub && <span className="text-[13px] font-medium text-muted-foreground">{sub}</span>}
    </Link>
  );
}

/** The full logo with the tagline "Carer and patient together". */
export function LogoFull({ width = 220, className }: { width?: number; className?: string }) {
  return (
    <Image
      src="/brand/logo.png"
      alt="CareKin: Carer and patient together"
      width={width}
      height={width}
      priority
      className={cn("h-auto", className)}
      style={{ width, height: "auto" }}
    />
  );
}

export function Avatar({
  label,
  className,
  tone = "mint",
}: {
  label: string;
  className?: string;
  tone?: "mint" | "amber" | "blue" | "green";
}) {
  const tones = {
    mint: "bg-mint text-teal",
    amber: "bg-[#FDF0DC] text-[#8A5A00]",
    blue: "bg-[#E4ECF8] text-[#2B4C84]",
    green: "bg-[#DDF3E4] text-[#1E7A3C]",
  };
  return (
    <span
      aria-hidden
      className={cn("flex shrink-0 items-center justify-center rounded-full font-bold", tones[tone], className)}
    >
      {label}
    </span>
  );
}

/** Stable colour per name so the same person always looks the same. */
export function toneFor(name: string): "mint" | "amber" | "blue" | "green" {
  const tones = ["amber", "blue", "green", "mint"] as const;
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return tones[h % tones.length];
}
