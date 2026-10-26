"use client";

import { CarerShell } from "@/components/carer/carer-shell";
import { SessionGate } from "@/components/session/session";

export default function CarerLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionGate roles={["caregiver"]}>
      <CarerShell>{children}</CarerShell>
    </SessionGate>
  );
}
