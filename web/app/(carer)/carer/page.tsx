import type { Metadata } from "next";
import { CarerHome } from "@/components/carer/carer-home";

export const metadata: Metadata = { title: "Your shift" };

export default function CarerPage() {
  return <CarerHome />;
}
