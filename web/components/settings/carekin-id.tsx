"use client";

import { useEffect, useId, useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The person's CareKin ID, with a copy button. Anyone can be given app access to someone's care,
 * so it's shown at sign-up, while waiting to be linked, and always in Settings.
 */
export function CareKinId({ id, hint, className }: { id: string; hint?: string; className?: string }) {
  const labelId = useId();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(t);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
    } catch {
      toast.error("Copy failed. Select the ID and copy it.");
    }
  }

  return (
    <div className={cn("space-y-3", className)}>
      <p id={labelId} className="ck-label">
        Your CareKin ID
      </p>
      <code aria-labelledby={labelId} className="block select-all break-all rounded-lg bg-mint-soft px-3 py-3 text-[15px]">
        {id}
      </code>
      {hint && <p className="ck-meta">{hint}</p>}
      <Button type="button" variant="secondary" onClick={copy}>
        {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy ID"}
      </Button>
    </div>
  );
}
