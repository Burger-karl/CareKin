"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { OtpForm } from "@/components/auth/otp-form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ApiError, authApi, errorMessage } from "@/lib/api/client";
import { homeFor } from "@/components/session/session";
import type { User } from "@/lib/types";

interface Sent {
  resend_after: number;
  debug_code?: string | null;
}

/** Only follow same-site paths, so a crafted link can't send people elsewhere. */
function safeNext(next: string | null) {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : null;
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [phone, setPhone] = useState("");
  const [sent, setSent] = useState<Sent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function sendCode(e: React.FormEvent) {
    e.preventDefault();
    if (phone.replace(/\D/g, "").length < 7) return setError("Enter your phone number.");
    setBusy(true);
    setError(null);
    try {
      setSent(await authApi<Sent>("otp/send", { phone }));
    } catch (err) {
      if (err instanceof ApiError && err.code === "account_not_found") {
        setError("We couldn't find an account with that number. Create one to get started.");
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function login(code: string) {
    const { user } = await authApi<{ user: User }>("login", { phone, code });
    qc.clear();
    router.replace(safeNext(params.get("next")) ?? homeFor(user.role));
    router.refresh();
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-title font-bold">Welcome back</h1>
        <p className="mt-1 text-muted-foreground">Sign in with your phone number. We&apos;ll text you a code.</p>
      </div>

      {sent ? (
        <div className="space-y-4">
          <OtpForm phone={phone} resendAfter={sent.resend_after} debugCode={sent.debug_code} onVerify={login} submitLabel="Sign in" />
          <button
            type="button"
            className="block w-full text-center text-meta font-semibold text-teal underline"
            onClick={() => setSent(null)}
          >
            Use a different number
          </button>
        </div>
      ) : (
        <form onSubmit={sendCode} className="space-y-4" noValidate>
          <Field label="Phone number" htmlFor="phone" error={error}>
            <Input
              id="phone"
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              placeholder="0803 123 4567"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              aria-invalid={!!error}
              aria-describedby={error ? "phone-error" : undefined}
            />
          </Field>
          <Button type="submit" size="lg" className="w-full" loading={busy}>
            Send code
          </Button>
        </form>
      )}

      <p className="text-center text-body text-muted-foreground">
        New to Care Kin?{" "}
        <Link href="/register" className="font-semibold text-teal underline">
          Create an account
        </Link>
      </p>
    </div>
  );
}
