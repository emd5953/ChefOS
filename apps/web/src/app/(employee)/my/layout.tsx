import type { Metadata, Viewport } from "next";
import { MyShell } from "@/components/my/my-shell";

export const metadata: Metadata = { title: { default: "My schedule", template: "%s | ChefOS" } };
export const viewport: Viewport = { themeColor: "#ffffff", width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function EmployeeLayout({ children }: { children: React.ReactNode }) {
  return <MyShell>{children}</MyShell>;
}
