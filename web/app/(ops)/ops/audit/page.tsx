import type { Metadata } from "next";
import { AuditLog } from "@/components/ops/audit-log";
import { OpsHeader } from "@/components/ops/ops-shell";

export const metadata: Metadata = { title: "Audit log" };

export default function AuditPage() {
  return (
    <div>
      <OpsHeader title="Audit log" subtitle="Every money movement and sensitive action." />
      <AuditLog />
    </div>
  );
}
