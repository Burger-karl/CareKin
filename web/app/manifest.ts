import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Care Kin",
    short_name: "Care Kin",
    description: "Care marketplace and safety layer for people living with sickle cell disease.",
    start_url: "/dashboard",
    display: "standalone",
    background_color: "#F3F8F8",
    theme_color: "#0F8577",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
  };
}
