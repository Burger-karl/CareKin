import type { Metadata, Viewport } from "next";
import { Golos_Text } from "next/font/google";
import { Providers } from "@/components/providers";
import "./globals.css";

const golos = Golos_Text({ subsets: ["latin"], variable: "--font-body", display: "swap" });

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title: { default: "Care Kin", template: "%s · Care Kin" },
  description: "Care marketplace and safety layer for people living with sickle cell disease.",
  applicationName: "Care Kin",
  manifest: "/manifest.webmanifest",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#0F8577",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={golos.variable}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
