import { AlertCircle, RotateCw } from "lucide-react";
import type { ReactNode } from "react";
import { errorMessage, isApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button } from "./button";

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center px-6 py-12 text-center", className)}>
      {icon && (
        <div className="mb-4 grid size-12 place-items-center rounded-2xl bg-muted text-foreground/60 [&_svg]:size-5">
          {icon}
        </div>
      )}
      <h3 className="text-[15px] font-semibold tracking-[-0.025em]">{title}</h3>
      {description && <p className="mt-1.5 max-w-sm text-[13px] leading-relaxed text-foreground/55">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  retrying,
  className,
  title = "Couldn’t load this",
}: {
  error: unknown;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
  title?: string;
}) {
  const requestId = isApiError(error) ? error.requestId : undefined;
  return (
    <div role="alert" className={cn("flex flex-col items-center px-6 py-12 text-center", className)}>
      <div className="mb-4 grid size-12 place-items-center rounded-2xl bg-destructive/10 text-destructive">
        <AlertCircle className="size-5" aria-hidden />
      </div>
      <h3 className="text-[15px] font-semibold tracking-[-0.025em]">{title}</h3>
      <p className="mt-1.5 max-w-sm text-[13px] leading-relaxed text-foreground/55">{errorMessage(error)}</p>
      {requestId && <p className="mt-1 text-[11px] text-foreground/35">Request ID {requestId}</p>}
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-5" onClick={onRetry} loading={retrying}>
          {!retrying && <RotateCw aria-hidden />}
          Try again
        </Button>
      )}
    </div>
  );
}
