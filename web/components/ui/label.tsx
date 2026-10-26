"use client";

import * as React from "react";
import * as LabelPrimitive from "@radix-ui/react-label";
import { cn } from "@/lib/utils";

const Label = React.forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root ref={ref} className={cn("ck-label", className)} {...props} />
));
Label.displayName = "Label";

/** Label + control + error text, wired for screen readers. */
function Field({
  label,
  htmlFor,
  error,
  hint,
  children,
  className,
}: {
  label: string;
  htmlFor: string;
  error?: string | null;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && !error && <p className="ck-meta">{hint}</p>}
      {error && (
        <p id={`${htmlFor}-error`} role="alert" className="text-meta font-medium text-crisis-ink">
          {error}
        </p>
      )}
    </div>
  );
}

export { Label, Field };
