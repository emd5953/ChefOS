"use client";

import { MapPin, Plug } from "lucide-react";
import Image from "next/image";
import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";
import { LinkButton } from "@/components/ui/button";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useLocationScope } from "@/lib/location-scope";
import { useConnections } from "@/lib/queries";
import type { Location } from "@/lib/types";

export function ConnectPosCard() {
  const { href } = useLocationScope();
  return (
    <section className="relative isolate overflow-hidden rounded-hero bg-ink text-white">
      <Image
        src="/assets/hero-kitchen.jpg"
        alt=""
        fill
        sizes="(min-width: 1280px) 1200px, 100vw"
        className="-z-20 object-cover object-[center_45%]"
      />
      <div aria-hidden className="absolute inset-0 -z-10 bg-gradient-to-r from-black/80 via-black/55 to-black/20" />
      <div className="px-6 py-14 sm:px-12 sm:py-20">
        <p className="eyebrow mb-4 !text-white/65">Sales &middot; Not connected</p>
        <h2 className="max-w-xl text-[34px] font-semibold leading-[1.05] tracking-[-0.055em] sm:text-[48px]">
          Connect your POS to see live sales
        </h2>
        <p className="mt-4 max-w-md text-[15px] leading-relaxed text-white/75">
          ChefOS reads orders and labor from the system you already run, so sales, average check, and labor % are on
          screen minutes after the ticket closes.
        </p>
        <LinkButton
          href={href("/app/integrations")}
          size="lg"
          className="mt-8 bg-white text-ink hover:bg-white/90"
        >
          <Plug aria-hidden /> Go to Integrations
        </LinkButton>
      </div>
    </section>
  );
}

/**
 * Resolves the preconditions every data view shares: locations loaded, at
 * least one location, and a connected POS. Renders `children(location)` once
 * ready, otherwise the right loading / error / empty state.
 */
export function PosGate({
  children,
  requirePos = true,
}: {
  children: (location: Location) => ReactNode;
  requirePos?: boolean;
}) {
  const scope = useLocationScope();
  const conns = useConnections();

  if (scope.isLoading || (requirePos && conns.isPending && !scope.isError)) return <GateSkeleton />;
  if (scope.isError) {
    return (
      <Card>
        <ErrorState title="Couldn’t load locations" error={scope.error} onRetry={scope.refetch} />
      </Card>
    );
  }
  if (!scope.current) {
    return (
      <Card>
        <EmptyState
          icon={<MapPin />}
          title="No locations yet"
          description="Add your first restaurant location to start tracking sales."
          action={<LinkButton href="/app/locations">Add a location</LinkButton>}
        />
      </Card>
    );
  }
  if (requirePos) {
    if (conns.isError) {
      return (
        <Card>
          <ErrorState
            title="Couldn’t load integrations"
            error={conns.error}
            onRetry={() => void conns.refetch()}
            retrying={conns.isFetching}
          />
        </Card>
      );
    }
    if (!conns.data || conns.data.length === 0) return <ConnectPosCard />;
  }
  return <>{children(scope.current)}</>;
}

function GateSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading">
      <Skeleton className="h-64 w-full rounded-card" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-32 rounded-card" />
        ))}
      </div>
    </div>
  );
}
