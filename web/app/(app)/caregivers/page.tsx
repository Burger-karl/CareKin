import type { Metadata } from "next";
import { Suspense } from "react";
import { CaregiverList } from "@/components/caregivers/caregiver-list";

export const metadata: Metadata = { title: "Find a Caregiver" };

export default function CaregiversPage() {
  return (
    <Suspense>
      <CaregiverList />
    </Suspense>
  );
}
