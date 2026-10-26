"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Zap } from "lucide-react";
import { toast } from "sonner";
import { getActiveEventId, getPending, startPress } from "@/lib/crisis";
import { cn } from "@/lib/utils";

/**
 * The crisis button. Pressing it stores the press on the device first, then opens the
 * crisis screen, which sends it (and keeps retrying if there's no connection).
 * Design rules: coral circle, two halo rings, always followed by the explainer line.
 */
export function CrisisButton({ patientId, className }: { patientId: string; className?: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function press() {
    if (busy) return;
    setBusy(true);
    try {
      // A press already in flight or a crisis already open: go back to it, don't start a second.
      if (!getPending() && !getActiveEventId()) await startPress(patientId);
      router.push("/crisis");
    } catch {
      toast.error("Couldn't start the alert. If you are in danger, call 112.");
      setBusy(false);
    }
  }

  return (
    <div className={cn("flex flex-col items-center", className)}>
      <button
        type="button"
        onClick={press}
        disabled={busy}
        aria-label="Crisis. One tap help. Shares your location and alerts your caregiver and emergency contacts."
        className="group relative flex size-[252px] items-center justify-center rounded-full bg-[#FCE7E8] transition-transform active:scale-[0.98] lg:size-[260px]"
      >
        <span aria-hidden className="absolute inset-[16px] animate-halo rounded-full bg-[#F8D3D5]" />
        <span className="relative flex size-[188px] flex-col items-center justify-center rounded-full bg-crisis text-white shadow-[0_8px_24px_rgba(214,56,64,0.35)] lg:size-[196px]">
          <Zap className="mb-2 size-9" strokeWidth={1.75} aria-hidden />
          <span className="text-[24px] font-bold tracking-wide">CRISIS</span>
          <span className="mt-0.5 text-[15px]">{busy ? "Starting…" : "One tap help"}</span>
        </span>
      </button>
      <p className="mt-3 max-w-[260px] text-center text-meta leading-snug text-muted-foreground">
        Shares location &amp; alerts <span className="font-semibold text-teal">caregiver + emergency</span> contacts
      </p>
    </div>
  );
}
