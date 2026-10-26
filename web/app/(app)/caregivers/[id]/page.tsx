import type { Metadata } from "next";
import { Suspense } from "react";
import { CaregiverProfile } from "@/components/caregivers/caregiver-profile";

export const metadata: Metadata = { title: "Caregiver profile" };

export default function CaregiverPage({ params }: { params: { id: string } }) {
  return (
    <Suspense>
      <CaregiverProfile id={params.id} />
    </Suspense>
  );
}
