import type { Metadata } from "next";
import { Integrations } from "@/components/integrations/integrations";

export const metadata: Metadata = { title: "Integrations" };

export default function IntegrationsPage() {
  return <Integrations />;
}
