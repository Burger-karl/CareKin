import type { UserRole } from "@/lib/types";

/**
 * Whether a signed-in person should be sent to /setup to create a care profile.
 * `skipSetup` is null until browser storage has been read: deciding before then would bounce
 * a family follower who just pressed Continue straight back to the setup form.
 */
export function shouldSendToSetup({
  role,
  patientCount,
  skipSetup,
}: {
  role: UserRole;
  patientCount: number | undefined;
  skipSetup: boolean | null;
}) {
  if (skipSetup !== false) return false;
  if (role !== "patient" && role !== "guardian") return false;
  return patientCount === 0;
}
