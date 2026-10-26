import type { Metadata } from "next";
import { ApplyForm } from "@/components/carer/apply-form";

export const metadata: Metadata = { title: "Apply to give care" };

export default function ApplyPage() {
  return <ApplyForm />;
}
