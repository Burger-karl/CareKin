"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ApiError, authApi, errorMessage } from "@/lib/api/client";

interface Sent {
  expires_in: number;
  resend_after: number;
  debug_code?: string | null;
}

/**
 * Six-digit code entry with a resend countdown. `onVerify` receives the typed code.
 * `debugCode` is only sent by a backend running with mock SMS (development); it never exists in production.
 */
export function OtpForm({
  phone,
  resendAfter,
  debugCode,
  onVerify,
  submitLabel = "Verify",
}: {
  phone: string;
  resendAfter: number;
  debugCode?: string | null;
  onVerify: (code: string) => Promise<void>;
  submitLabel?: string;
}) {
  const [testCode, setTestCode] = useState(debugCode ?? null);
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
      setTestCode(r.debug_code ?? null);
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
      {testCode && (
        <div className="flex items-center justify-between gap-3 rounded-xl border-[1.5px] border-dashed border-amber bg-[#FFF8E6] px-4 py-3 text-sm">
          <span>
            Test mode code: <strong className="tracking-widest">{testCode}</strong>
          </span>
          <button type="button" onClick={() => setCode(testCode)} className="font-semibold text-teal underline">
            Use it
          </button>
        </div>
      )}
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
