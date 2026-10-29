"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ConsentStep, type ConsentResult } from "@/components/auth/consent-step";
import { OtpForm } from "@/components/auth/otp-form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ApiError, api, authApi, errorMessage } from "@/lib/api/client";
import type { ConsentType, User } from "@/lib/types";

type Step = "details" | "who" | "verify";

interface Registered {
  resend_after: number;
  debug_code?: string | null;
}

function maskPhone(phone: string) {
  const d = phone.replace(/[^\d+]/g, "");
  return d.length > 7 ? `${d.slice(0, d.startsWith("+") ? 7 : 4)} ••• ${d.slice(-4)}` : d;
}

const steps = ["details", "who", "verify"] as const;

function StepBar({ step }: { step: Step }) {
  const i = steps.indexOf(step);
  return (
    <div className="space-y-3">
      <p className="text-right text-sm font-semibold text-muted-foreground">Step {i + 1} of 3</p>
      <div className="flex gap-2" aria-hidden>
        {steps.map((s, n) => (
          <span key={s} className={`h-1.5 flex-1 rounded-full ${n <= i ? "bg-primary" : "bg-[#D3E5E3]"}`} />
        ))}
      </div>
    </div>
  );
}

export function RegisterFlow() {
  const router = useRouter();
  const qc = useQueryClient();
  const [step, setStep] = useState<Step>("details");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [choice, setChoice] = useState<ConsentResult | null>(null);
  const [reg, setReg] = useState<Registered | null>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  function details(e: React.FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (name.trim().split(/\s+/).filter(Boolean).length < 1 || name.trim().length < 2) next.name = "Enter your full name.";
    if (phone.replace(/\D/g, "").length < 7) next.phone = "Enter a valid phone number.";
    setErrors(next);
    if (Object.keys(next).length === 0) setStep("who");
  }

  async function register(result: ConsentResult) {
    setBusy(true);
    setFormError(null);
    try {
      const r = await authApi<Registered>("register", {
        phone,
        full_name: name.trim(),
        role: result.who.role,
      });
      setChoice(result);
      setReg(r);
      setStep("verify");
    } catch (err) {
      if (err instanceof ApiError && err.code === "phone_taken") {
        setFormError("That number already has an account. Sign in instead.");
      } else if (err instanceof ApiError && err.code === "validation_error") {
        const fe = err.fieldErrors;
        if (fe.phone || fe.full_name) {
          setErrors({ ...(fe.phone && { phone: fe.phone }), ...(fe.full_name && { name: fe.full_name }) });
          setStep("details");
        } else setFormError(err.message);
      } else {
        setFormError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function verify(code: string) {
    if (!choice) return;
    const { user } = await authApi<{ user: User }>("otp/verify", { phone, code });
    // Record each consent the person ticked. Terms always goes first.
    const failed: ConsentType[] = [];
    for (const consent_type of choice.consents) {
      try {
        await api("auth/consent", { body: { consent_type, granted: true } });
      } catch {
        failed.push(consent_type);
      }
    }
    qc.clear();
    if (failed.length > 0) {
      router.replace("/onboarding?resume=1");
    } else if (user.role === "caregiver") {
      router.replace("/carer/apply");
    } else {
      router.replace(choice.who.id === "family" ? "/setup?mode=family" : "/setup");
    }
    router.refresh();
  }

  return (
    <div className="space-y-6">
      {step === "details" && (
        <>
          <StepBar step={step} />
          <div>
            <h1 className="text-title font-bold leading-tight">Create your account</h1>
            <p className="mt-2 text-muted-foreground">We&apos;ll text a code to confirm your number.</p>
          </div>
          <form onSubmit={details} className="space-y-4" noValidate>
            <Field label="Full name" htmlFor="name" error={errors.name}>
              <Input
                id="name"
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                aria-invalid={!!errors.name}
                aria-describedby={errors.name ? "name-error" : undefined}
              />
            </Field>
            <Field label="Phone number" htmlFor="phone" error={errors.phone}>
              <Input
                id="phone"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                placeholder="0803 123 4567"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                aria-invalid={!!errors.phone}
                aria-describedby={errors.phone ? "phone-error" : undefined}
              />
            </Field>
            <Button type="submit" size="lg" className="w-full">
              Continue
            </Button>
          </form>
          <p className="text-center text-body text-muted-foreground">
            Already have an account?{" "}
            <Link href="/login" className="font-semibold text-teal underline">
              Sign in
            </Link>
          </p>
        </>
      )}

      {step === "who" && (
        <>
          <ConsentStep
            subtitle={`Phone ${maskPhone(phone)}`}
            submitting={busy}
            onSubmit={register}
            error={formError}
          />
          {formError?.includes("Sign in") && (
            <p className="text-center">
              <Link href="/login" className="font-semibold text-teal underline">
                Go to sign in
              </Link>
            </p>
          )}
          <button
            type="button"
            className="block w-full text-center text-meta font-semibold text-teal underline"
            onClick={() => setStep("details")}
          >
            Back
          </button>
        </>
      )}

      {step === "verify" && reg && (
        <>
          <StepBar step={step} />
          <div>
            <h1 className="text-title font-bold leading-tight">Verify your phone</h1>
            <p className="mt-2 text-muted-foreground">Phone {maskPhone(phone)}</p>
          </div>
          <OtpForm phone={phone} resendAfter={reg.resend_after} debugCode={reg.debug_code} onVerify={verify} submitLabel="Verify & continue" />
        </>
      )}
    </div>
  );
}
