"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ConsentStep, type ConsentResult, type WhoOption } from "@/components/auth/consent-step";
import { PageSpinner, homeFor } from "@/components/session/session";
import { api, errorMessage } from "@/lib/api/client";
import { keys, useMe } from "@/lib/api/queries";

/** Existing accounts that still owe a required consent (new policy version, or skipped at sign-up). */
export default function OnboardingPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!me.data) return <PageSpinner />;
  const role = me.data.role;
  const who: WhoOption["id"] = role === "caregiver" ? "caregiver" : role === "patient" ? "myself" : "child";

  async function submit({ consents }: ConsentResult) {
    setBusy(true);
    setError(null);
    try {
      for (const consent_type of consents) {
        await api("auth/consent", { body: { consent_type, granted: true } });
      }
      await qc.invalidateQueries({ queryKey: keys.me });
      router.replace(homeFor(role));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return <ConsentStep fixedWho={who} step={null} submitting={busy} onSubmit={submit} error={error} />;
}
