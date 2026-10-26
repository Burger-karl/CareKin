"use client";

import { OpsShell } from "@/components/ops/ops-shell";
import { SessionGate } from "@/components/session/session";

export default function OpsLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionGate roles={["ops"]}>
      <OpsShell>{children}</OpsShell>
    </SessionGate>
  );
}
