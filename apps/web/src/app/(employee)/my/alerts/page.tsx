import type { Metadata } from "next";
import { MyAlerts } from "@/components/my/alerts";

export const metadata: Metadata = { title: "Alerts" };

export default function MyAlertsPage() {
  return <MyAlerts />;
}
