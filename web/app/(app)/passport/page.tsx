import type { Metadata } from "next";
import { PassportView } from "@/components/passport/passport-view";

export const metadata: Metadata = { title: "Health Passport" };

export default function PassportPage() {
  return <PassportView />;
}
