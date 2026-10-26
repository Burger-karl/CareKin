import type { Metadata } from "next";
import { CarerProfile } from "@/components/carer/carer-profile";

export const metadata: Metadata = { title: "Profile" };

export default function CarerProfilePage() {
  return <CarerProfile />;
}
