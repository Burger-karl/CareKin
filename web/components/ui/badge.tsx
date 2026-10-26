import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1 rounded-full px-3 py-1 text-[13px] font-medium leading-tight", {
  variants: {
    variant: {
      mint: "bg-mint text-teal",
      neutral: "bg-muted text-foreground/80",
      crisis: "bg-crisis px-2.5 py-0.5 text-xs font-bold text-white",
      amber: "bg-amber-soft font-semibold text-amber-ink",
      info: "bg-[#E4ECF8] text-[#2B4C84]",
      success: "bg-[#DDF3E4] text-[#1E7A3C]",
      danger: "bg-crisis-soft font-semibold text-crisis-ink",
    },
  },
  defaultVariants: { variant: "mint" },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
