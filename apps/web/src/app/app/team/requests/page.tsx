import type { Metadata } from "next";
import { RequestsInbox } from "@/components/team/requests";

export const metadata: Metadata = { title: "Requests" };

export default function RequestsPage() {
  return <RequestsInbox />;
}
