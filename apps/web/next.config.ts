import type { NextConfig } from "next";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8080";

// Client-side routes of the static marketing site (public/app.js). Every one
// is served by public/index.html so deep links work.
const marketingRoutes = [
  "/team",
  "/commerce",
  "/inventory",
  "/integrations",
  "/pricing",
  "/mobile-app",
  "/company",
  "/contact",
  "/blog",
  "/blog/:slug",
  "/privacy",
  "/terms",
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  devIndicators: false,
  async rewrites() {
    return {
      beforeFiles: [
        { source: "/", destination: "/index.html" },
        ...marketingRoutes.map((source) => ({ source, destination: "/index.html" })),
      ],
      afterFiles: [{ source: "/api/:path*", destination: `${API_ORIGIN}/:path*` }],
      fallback: [],
    };
  },
};

export default nextConfig;
