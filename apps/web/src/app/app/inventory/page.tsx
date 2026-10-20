import type { Metadata } from "next";
import { Inventory } from "@/components/inventory/inventory";

export const metadata: Metadata = { title: "Inventory" };

export default function InventoryPage() {
  return <Inventory />;
}
