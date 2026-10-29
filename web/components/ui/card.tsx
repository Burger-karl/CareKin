import * as React from "react";
import { cn } from "@/lib/utils";

const TONES = {
  white: "",
  mint: "border-[#BEDCD8] bg-mint",
  soft: "bg-gradient-to-br from-mint-soft to-white",
  crisis: "border-[#F3B8BB] bg-gradient-to-br from-[#FDECEC] to-white",
  amber: "border-[#F2D9A4] bg-gradient-to-br from-amber-soft to-white",
  blue: "border-[#C9D9F2] bg-gradient-to-br from-[#E9F0FB] to-white",
  teal: "border-transparent bg-gradient-to-br from-[#0F8577] to-[#0B6B60] text-white",
} as const;

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Background colour. Defaults to plain white. */
  tone?: keyof typeof TONES;
  /** Start the entrance this many milliseconds late. Cards already stagger by position; use this to override it. */
  delay?: number;
  /** Raise the card slightly when pointed at. */
  lift?: boolean;
}

const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, tone = "white", delay, lift = false, style, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "ck-card p-5",
        TONES[tone],
        lift && "transition duration-300 hover:-translate-y-1 hover:shadow-lg hover:shadow-teal/10",
        className,
      )}
      style={delay !== undefined ? { ...style, animationDelay: `${delay}ms` } : style}
      {...props}
    />
  ),
);
Card.displayName = "Card";

const CardHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("mb-4 flex items-center justify-between gap-3", className)} {...props} />
);

const CardTitle = ({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) => (
  <h2 className={cn("text-card font-bold text-foreground", className)} {...props} />
);

/** Mint stat tile used on passport, shift and escrow cards. */
const Tile = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("rounded-lg bg-mint-soft px-3.5 py-3", className)} {...props} />
);

export { Card, CardHeader, CardTitle, Tile };
