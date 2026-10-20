"use client";

import { CalendarDays } from "lucide-react";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/states";
import { useSession } from "@/lib/auth";

export function MySchedule() {
  const { user } = useSession();
  return (
    <>
      <p className="eyebrow">Hi, {user.display_name.split(" ")[0]}</p>
      <h1 className="mt-2 text-[30px] font-semibold leading-tight tracking-[-0.05em]">Your schedule</h1>
      <Card className="mt-5">
        <EmptyState
          icon={<CalendarDays />}
          title="No published schedule yet"
          description="When your manager publishes the week, your shifts show up here."
        />
      </Card>
    </>
  );
}
