import Link from "next/link";
import { cn } from "@/lib/utils";

export function Logo({ className, sub, href = "/" }: { className?: string; sub?: string; href?: string }) {
  return (
    <Link href={href} className={cn("inline-flex items-center gap-2", className)} aria-label="Care Kin home">
      <LogoMark />
      <span className="text-[22px] font-bold tracking-tight">
        <span className="text-teal">Care</span> <span className="text-ink">Kin</span>
      </span>
      {sub && <span className="text-[13px] font-medium text-muted-foreground">{sub}</span>}
    </Link>
  );
}

/** The heart mark, cropped from the full logo (public/logo.jpg) with CSS. */
export function LogoMark({ size = 36 }: { size?: number }) {
  const bg = size / 0.43;
  return (
    <span
      aria-hidden
      className="inline-block shrink-0 bg-no-repeat"
      style={{
        width: size,
        height: size * 1.14,
        backgroundImage: "url(/logo.jpg)",
        backgroundSize: `${bg}px ${bg}px`,
        backgroundPosition: `${-0.29 * bg}px ${-0.13 * bg}px`,
      }}
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
