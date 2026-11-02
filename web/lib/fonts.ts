import { IBM_Plex_Sans } from "next/font/google";

/** The signed-in app's typeface (the public site uses Golos Text). */
export const appFont = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-app",
  display: "swap",
});
