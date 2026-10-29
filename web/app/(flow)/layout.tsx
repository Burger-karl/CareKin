import { AuthShell } from "@/components/auth/auth-shell";

export default function FlowLayout({ children }: { children: React.ReactNode }) {
  return <AuthShell wide>{children}</AuthShell>;
}
