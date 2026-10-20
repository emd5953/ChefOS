import type { MetadataRoute } from "next";

// Lets employees add the /my view to their home screen (ADR 0004: mobile is a web view).
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "ChefOS",
    short_name: "ChefOS",
    description: "Your schedule, requests, and alerts.",
    start_url: "/my",
    scope: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#ffffff",
    icons: [{ src: "/assets/logo.png", sizes: "250x250", type: "image/png" }],
  };
}
