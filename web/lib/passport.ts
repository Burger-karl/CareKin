import { api } from "@/lib/api/client";

/** Fetches the one-page PDF through the proxy and hands it to the browser as a download. */
export async function downloadPassportPdf(patientId: string) {
  const blob = await api<Blob>(`passport/${patientId}/export`);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "carekin-health-passport.pdf";
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Give the browser a moment to start the download before the blob goes away.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const ENTRY_LABEL = {
  crisis: "Pain crisis",
  trigger: "Trigger",
  visit: "Hospital visit",
  transfusion: "Transfusion",
  medication: "Medication",
} as const;
