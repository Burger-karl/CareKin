"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ApiError, authApi, errorMessage } from "@/lib/api/client";

interface Sent {
  expires_in: number;
  resend_after: number;
}

/** Six-digit code entry with a resend countdown. `onVerify` receives the typed code. */
export function OtpForm({
  phone,
  resendAfter,
  onVerify,
  submitLabel = "Verify",
}: {
  phone: string;
  resendAfter: number;
  onVerify: (code: string) => Promise<void>;
  submitLabel?: string;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState(resendAfter);
  const [resending, setResending] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => input.current?.focus(), []);
  useEffect(() => {
    if (wait <= 0) return;
    const t = setInterval(() => setWait((w) => Math.max(0, w - 1)), 1000);
    return () => clearInterval(t);
  }, [wait]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!/^\d{4,8}$/.test(code)) return setError("Enter the code we texted you.");
    setBusy(true);
    setError(null);
    try {
      await onVerify(code);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  async function resend() {
    setResending(true);
    setError(null);
    try {
      const r = await authApi<Sent>("otp/send", { phone });
      setWait(r.resend_after);
    } catch (err) {
      if (err instanceof ApiError && err.retryAfter) setWait(err.retryAfter);
      setError(errorMessage(err));
    } finally {
      setResending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <Field label="Verification code" htmlFor="otp" error={error} hint={`We texted a code to ${phone}.`}>
        <Input
          id="otp"
          ref={input}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={8}
          placeholder="123456"
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          aria-invalid={!!error}
          aria-describedby={error ? "otp-error" : undefined}
          className="text-center text-xl tracking-[0.4em]"
        />
      </Field>
      <Button type="submit" size="lg" className="w-full" loading={busy}>
        {submitLabel}
      </Button>
      <p className="text-center text-meta text-muted-foreground">
        {wait > 0 ? (
          <>Resend code in {wait}s</>
        ) : (
          <button type="button" onClick={resend} disabled={resending} className="font-semibold text-teal underline">
            {resending ? "Sending…" : "Resend code"}
          </button>
        )}
      </p>
    </form>
  );
}
