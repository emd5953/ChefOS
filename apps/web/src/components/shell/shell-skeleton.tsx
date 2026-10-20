import { Skeleton } from "@/components/ui/skeleton";

export function ShellSkeleton() {
  return (
    <div className="min-h-dvh lg:pl-[264px]" role="status" aria-label="Loading workspace">
      <div className="fixed inset-y-0 left-0 hidden w-[264px] flex-col gap-4 border-r border-border p-5 lg:flex">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="mt-4 h-16 w-full rounded-xl" />
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-9 w-full rounded-lg" />
        ))}
      </div>
      <div className="mx-auto max-w-[1280px] space-y-6 px-5 py-10 sm:px-8">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-56 w-full rounded-card" />
      </div>
    </div>
  );
}
