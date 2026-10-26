import type { Metadata } from "next";
import { Suspense } from "react";
import { SetupFlow } from "./setup-flow";

export const metadata: Metadata = { title: "Set up care" };

export default function SetupPage() {
  return (
    <Suspense>
      <SetupFlow />
    </Suspense>
  );
}
