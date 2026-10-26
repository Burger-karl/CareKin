import type { Metadata } from "next";
import { SessionGate } from "@/components/session/session";
import { CrisisScreen } from "@/components/crisis/crisis-screen";

export const metadata: Metadata = { title: "Crisis mode" };

export default function CrisisPage() {
  return (
    <SessionGate roles={["patient", "guardian"]}>
      <CrisisScreen />
    </SessionGate>
  );
}
