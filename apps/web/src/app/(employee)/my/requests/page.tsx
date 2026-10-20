import type { Metadata } from "next";
import { MyRequests } from "@/components/my/requests";

export const metadata: Metadata = { title: "Requests" };

export default function MyRequestsPage() {
  return <MyRequests />;
}
