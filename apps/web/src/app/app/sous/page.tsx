import type { Metadata } from "next";
import { Sous } from "@/components/sous/sous";

export const metadata: Metadata = { title: "Sous" };

export default function SousPage() {
  return <Sous />;
}
