"use client";

import Link from "next/link";
import { Check, Clock, LogOut } from "lucide-react";
import { AvailabilityCard } from "@/components/carer/availability-card";
import { useSession } from "@/components/session/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useMyCaregiver } from "@/lib/api/carer-queries";
import { formatMoney, humanize } from "@/lib/utils";

const CHECK_LABEL: Record<string, string> = { nin: "NIN check", background: "Background check" };

export function CarerProfile() {
  const { me, signOut } = useSession();
  const { caregiver, id, loading } = useMyCaregiver();

  return (
    <div className="space-y-4">
      <h1 className="text-[30px] font-bold leading-9">Profile</h1>
      <Card>
        <p className="text-[19px] font-bold">{me.full_name}</p>
        <p className="ck-meta">{me.phone}</p>
        {caregiver && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Badge>{humanize(caregiver.tier)}</Badge>
            <Badge variant={caregiver.status === "approved" ? "success" : "amber"}>{humanize(caregiver.status)}</Badge>
            {caregiver.hourly_rate_minor != null && <span className="text-[15px] font-semibold">{formatMoney(caregiver.hourly_rate_minor)}/hr</span>}
          </div>
        )}
      </Card>

      {loading && <Skeleton className="h-40 rounded-xl" />}
      {!loading && !id && (
        <Card className="text-center">
          <p className="font-bold">No application yet</p>
          <Button asChild className="mt-3">
            <Link href="/carer/apply">Apply to give care</Link>
          </Button>
        </Card>
      )}

      {id && <AvailabilityCard caregiverId={id} />}

      {caregiver && "verification_checks" in caregiver && (
        <Card>
          <CardTitle className="mb-3">Your checks</CardTitle>
          <ul className="space-y-3">
            {(caregiver as unknown as { verification_checks: { check_type: string; status: string }[] }).verification_checks.map((c) => (
              <li key={c.check_type} className="flex items-center gap-3 text-[16px]">
                {c.status === "passed" ? (
                  <Check className="size-5 text-teal" aria-hidden />
                ) : (
                  <Clock className="size-5 text-amber" aria-hidden />
                )}
                <span className="flex-1">{CHECK_LABEL[c.check_type] ?? humanize(c.check_type)}</span>
                <span className="ck-meta">{humanize(c.status)}</span>
              </li>
            ))}
            {(caregiver as unknown as { verification_checks: unknown[] }).verification_checks.length === 0 && (
              <li className="text-body text-muted-foreground">CareKin hasn&apos;t started your checks yet.</li>
            )}
          </ul>
        </Card>
      )}

      <Button variant="outline" onClick={() => void signOut()}>
        <LogOut /> Sign out
      </Button>
    </div>
  );
}
