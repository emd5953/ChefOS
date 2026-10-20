import type { Metadata } from "next";
import { Team } from "@/components/team/team";

export const metadata: Metadata = { title: "Team" };

export default function TeamPage() {
  return <Team />;
}
