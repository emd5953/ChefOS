import type { Metadata } from "next";
import { Items } from "@/components/items/items";

export const metadata: Metadata = { title: "Menu items" };

export default function ItemsPage() {
  return <Items />;
}
