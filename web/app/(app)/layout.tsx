"use client";

import { SessionGate } from "@/components/session/session";
import { AppShell } from "@/components/shell/app-shell";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionGate roles={["patient", "guardian", "family_abroad", "facility_admin"]}>
      <AppShell>{children}</AppShell>
    </SessionGate>
  );
}
