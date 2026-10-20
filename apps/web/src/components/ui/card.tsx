import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";

export function Card({ className, ...props }: ComponentProps<"section">) {
  return (
    <section
      className={cn("rounded-card border border-border bg-card text-card-foreground", className)}
      {...props}
    />
  );
}

export function CardHeader({
  title,
  description,
  overline,
  action,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  overline?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-3 p-5 pb-0 sm:p-6 sm:pb-0", className)}>
      <div className="min-w-0">
        {overline && <p className="eyebrow mb-1.5">{overline}</p>}
        <h2 className="text-[17px] font-semibold tracking-[-0.03em]">{title}</h2>
        {description && <p className="mt-1 text-[13px] text-foreground/55">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function CardBody({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("p-5 sm:p-6", className)} {...props} />;
}
