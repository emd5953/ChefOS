import type { Metadata } from "next";
import { Sales } from "@/components/sales/sales";

export const metadata: Metadata = { title: "Sales" };

export default function SalesPage() {
  return <Sales />;
}
