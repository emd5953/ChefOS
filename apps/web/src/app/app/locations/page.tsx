import type { Metadata } from "next";
import { Locations } from "@/components/locations/locations";

export const metadata: Metadata = { title: "Locations" };

export default function LocationsPage() {
  return <Locations />;
}
