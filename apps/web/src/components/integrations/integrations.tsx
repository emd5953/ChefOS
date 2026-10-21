"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plug } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage } from "@/lib/api";
import { canManage, useSession } from "@/lib/auth";
import { useConnections, keys } from "@/lib/queries";
import { ConnectionView } from "./connection-view";
import { ApiKeysCard } from "./push-keys";

const ERROR_COPY: Record<string, string> = {
  access_denied: "Square access was declined, so nothing was connected.",
  invalid_state: "That connection attempt expired. Please try connecting again.",
  state_mismatch: "That connection attempt expired. Please try connecting again.",
};

export function Integrations() {
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const handled = useRef<string | null>(null);

  // ?connected=<id> / ?error=<code> come back from the OAuth callback redirect.
  useEffect(() => {
    const connected = search.get("connected");
    const error = search.get("error");
    const token = connected ?? error;
    if (!token || handled.current === token) return;
    handled.current = token;
    if (connected) {
      toast("Square connected. Your data is syncing now.", "success");
      void qc.invalidateQueries({ queryKey: keys.connections });
    } else if (error) {
      toast(ERROR_COPY[error] ?? `Couldn’t connect Square (${error}).`, "error");
    }
    const sp = new URLSearchParams(search.toString());
    sp.delete("connected");
    sp.delete("error");
    const qs = sp.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [search, pathname, router, toast, qc]);

  const conns = useConnections();
  const first = conns.data?.[0];

  return (
    <>
      <PageHeader
        overline="Integrations"
        title="Integrations"
        description="Connect the systems that run service. ChefOS keeps the data fresh and tells you when it isn’t."
      />
      {conns.isPending ? (
        <div className="space-y-4" role="status" aria-label="Loading integrations">
          <Skeleton className="h-40 rounded-card" />
          <Skeleton className="h-64 rounded-card" />
        </div>
      ) : conns.isError ? (
        <Card>
          <ErrorState error={conns.error} onRetry={() => void conns.refetch()} retrying={conns.isFetching} />
        </Card>
      ) : first ? (
        <ConnectionView connectionId={first.id} />
      ) : (
        <ConnectSquareCard />
      )}
      <ApiKeysCard />
    </>
  );
}

export function useStartSquare() {
  const toast = useToast();
  return useMutation({
    mutationFn: () =>
      api<{ authorize_url: string }>("/v1/integrations/square/authorize", { method: "POST" }),
    onSuccess: ({ authorize_url }) => window.location.assign(authorize_url),
    onError: (e) => toast(errorMessage(e), "error"),
  });
}

function ConnectSquareCard() {
  const { role } = useSession();
  const start = useStartSquare();
  const allowed = canManage(role);
  return (
    <Card className="overflow-hidden">
      <CardBody className="flex flex-col gap-8 p-6 sm:flex-row sm:items-center sm:justify-between sm:p-8">
        <div className="flex gap-5">
          <SquareMark />
          <div>
            <p className="eyebrow mb-2">Point of sale</p>
            <h2 className="text-2xl font-semibold tracking-[-0.05em]">Square</h2>
            <p className="mt-2 max-w-md text-[14px] leading-relaxed text-foreground/55">
              Import orders, menu items, team members, and timecards. Sales and labor appear in ChefOS within minutes
              of the ticket closing, and history backfills automatically.
            </p>
            {!allowed && (
              <p className="mt-3 text-xs text-foreground/45">Only owners and admins can connect integrations.</p>
            )}
          </div>
        </div>
        <Button size="lg" onClick={() => start.mutate()} loading={start.isPending} disabled={!allowed} className="shrink-0">
          <Plug aria-hidden /> Connect Square
        </Button>
      </CardBody>
    </Card>
  );
}

export function SquareMark() {
  return (
    <span
      aria-hidden
      className="grid size-14 shrink-0 place-items-center rounded-2xl bg-ink text-white"
    >
      <span className="size-6 rounded-[5px] border-[2.5px] border-white">
        <span className="m-auto mt-[5px] block size-1.5 rounded-[1px] bg-white" />
      </span>
    </span>
  );
}
