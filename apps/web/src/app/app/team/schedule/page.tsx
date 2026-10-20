import type { Metadata } from "next";
import { ScheduleBuilder } from "@/components/team/schedule/builder";

export const metadata: Metadata = { title: "Schedule" };

export default function SchedulePage() {
  return <ScheduleBuilder />;
}
