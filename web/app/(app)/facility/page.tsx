"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { FacilityConsole } from "@/components/facilities/facility-console";
import { useSession } from "@/components/session/session";

export default function FacilityPage() {
  const router = useRouter();
  const { me } = useSession();

  // Only facility admins manage a listing; everyone else has the finder.
  useEffect(() => {
    if (me.role !== "facility_admin") router.replace("/facilities");
  }, [me.role, router]);

  if (me.role !== "facility_admin") return null;
  return <FacilityConsole />;
}
