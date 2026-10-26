import type { Metadata } from "next";
import { FacilityFinder } from "@/components/facilities/finder";

export const metadata: Metadata = { title: "SCD Facilities" };

export default function FacilitiesPage() {
  return <FacilityFinder />;
}
