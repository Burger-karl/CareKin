"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/shell/app-shell";
import { useSession } from "@/components/session/session";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ApiError, api, errorMessage } from "@/lib/api/client";
import { keys } from "@/lib/api/queries";
import type { ConsentType, Me } from "@/lib/types";

const CONSENTS: { type: ConsentType; title: string; body: string; required?: boolean; patientOnly?: boolean }[] = [
  { type: "health_data", title: "Care profile & health passport", body: "Encrypted. Shared only when you say so.", required: true, patientOnly: true },
  { type: "location", title: "Location, only during a crisis", body: "Sent to emergency contacts and your caregiver." },
  { type: "llm_processing", title: "WhatsApp companion", body: "Hydration, medication & appointment check-ins." },
];

export function SettingsView() {
  const { me, signOut } = useSession();
  const qc = useQueryClient();
  const [name, setName] = useState(me.full_name);
  const [email, setEmail] = useState(me.email ?? "");
  const [lang, setLang] = useState(me.preferred_language);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const consent = useMutation({
    mutationFn: ({ type, granted }: { type: ConsentType; granted: boolean }) =>
      api("auth/consent", { body: { consent_type: type, granted } }),
    onSuccess: () => {
      toast.success("Saved.");
      void qc.invalidateQueries({ queryKey: keys.me });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const save = useMutation({
    mutationFn: () =>
      api<Me>("auth/me", {
        method: "PATCH",
        body: { full_name: name.trim(), email: email.trim() || null, preferred_language: lang },
      }),
    onSuccess: () => {
      setErrors({});
      toast.success("Profile updated.");
      void qc.invalidateQueries({ queryKey: keys.me });
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "validation_error") setErrors(e.fieldErrors);
      else toast.error(errorMessage(e));
    },
  });

  const rows = CONSENTS.filter((c) => !c.patientOnly || me.role === "patient" || me.role === "guardian");

  return (
    <div>
      <PageHeader title="Settings" subtitle="Your details and what you share" />
      <div className="mx-auto max-w-xl space-y-4 lg:mx-0">
        <Card>
          <CardTitle className="mb-4">Your details</CardTitle>
          <form
            className="space-y-4"
            noValidate
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate();
            }}
          >
            <Field label="Full name" htmlFor="s-name" error={errors.full_name}>
              <Input id="s-name" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Phone" htmlFor="s-phone" hint="Verified. Contact support to change it.">
              <Input id="s-phone" value={me.phone} disabled />
            </Field>
            <Field label="Email (optional)" htmlFor="s-email" error={errors.email}>
              <Input id="s-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label="Language" htmlFor="s-lang">
              <Select id="s-lang" value={lang} onChange={(e) => setLang(e.target.value as Me["preferred_language"])}>
                <option value="en">English</option>
                <option value="ig">Igbo</option>
                <option value="pcm">Pidgin</option>
              </Select>
            </Field>
            <Button type="submit" loading={save.isPending}>
              Save changes
            </Button>
          </form>
        </Card>

        <Card>
          <CardTitle className="mb-1">What we collect &amp; why</CardTitle>
          <p className="ck-meta mb-4">
            Consent is recorded under the Nigeria Data Protection Act. Change it any time.{" "}
            <Link href="/privacy" className="font-semibold text-teal underline">
              Privacy policy
            </Link>
          </p>
          <ul className="divide-y divide-line">
            {rows.map((c) => (
              <li key={c.type} className="flex items-center justify-between gap-4 py-3 first:pt-0 last:pb-0">
                <div>
                  <p id={`c-${c.type}`} className="text-[16px] font-bold">
                    {c.title}
                  </p>
                  <p className="ck-meta">{c.body}</p>
                </div>
                <Switch
                  checked={me.consents[c.type]}
                  disabled={consent.isPending}
                  onCheckedChange={(granted) => consent.mutate({ type: c.type, granted })}
                  aria-labelledby={`c-${c.type}`}
                />
              </li>
            ))}
          </ul>
        </Card>

        <Button variant="outline" onClick={() => void signOut()}>
          <LogOut /> Sign out
        </Button>
      </div>
    </div>
  );
}
