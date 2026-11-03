import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "CareKin",
    short_name: "CareKin",
    description: "Care marketplace and safety layer for people living with sickle cell disease.",
    start_url: "/dashboard",
    display: "standalone",
    background_color: "#F3F8F8",
    theme_color: "#0F8577",
    icons: [
      { src: "/icon.png", sizes: "256x256", type: "image/png", purpose: "any" },
      { src: "/apple-icon.png", sizes: "180x180", type: "image/png" },
    ],
  };
}
