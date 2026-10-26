import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

const config: Config = {
  darkMode: ["class"],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "1rem" },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--border))",
        ring: "hsl(var(--primary))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--primary))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--primary))" },
        destructive: { DEFAULT: "hsl(var(--crisis))", foreground: "#fff" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--foreground))" },
        popover: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--foreground))" },
        // CareKin palette (design foundations)
        teal: { DEFAULT: "#0F8577", dark: "#0B6B60" },
        mint: { DEFAULT: "#DCEFEC", soft: "#EAF4F2" },
        crisis: { DEFAULT: "#D63840", soft: "#FDECEC", ink: "#A82A31" },
        amber: { DEFAULT: "#F2A516", soft: "#FEF3DD", ink: "#8A5A00" },
        ink: "#13232B",
        ground: "#F3F8F8",
        line: "#D3E5E3",
      },
      fontFamily: { sans: ["var(--font-dm-sans)", "system-ui", "sans-serif"] },
      borderRadius: { xl: "20px", "2xl": "24px", lg: "14px", md: "10px", sm: "6px" },
      fontSize: {
        meta: ["13px", "18px"],
        body: ["15px", "22px"],
        card: ["17px", "24px"],
        title: ["30px", "36px"],
      },
      keyframes: {
        halo: {
          "0%, 100%": { transform: "scale(1)", opacity: "0.9" },
          "50%": { transform: "scale(1.04)", opacity: "1" },
        },
        "accordion-down": { from: { height: "0" }, to: { height: "var(--radix-accordion-content-height)" } },
      },
      animation: { halo: "halo 2.4s ease-in-out infinite" },
    },
  },
  plugins: [animate],
};

export default config;
