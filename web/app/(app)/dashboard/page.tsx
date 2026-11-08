"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { FamilyHome } from "@/components/dashboard/family-home";
import { PatientHome } from "@/components/dashboard/patient-home";
import { useSession } from "@/components/session/session";

export default function DashboardPage() {
  const router = useRouter();
  const { me, patient } = useSession();

  useEffect(() => {
    if (me.role === "facility_admin") router.replace("/facility");
  }, [me.role, router]);

  if (me.role === "facility_admin") return null;
  // No patient yet means a family member waiting to be linked: show them their CareKin ID.
  if (!patient || me.role === "family_abroad" || patient.access === "family") return <FamilyHome />;
  return <PatientHome />;
}
