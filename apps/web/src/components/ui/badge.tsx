import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold leading-none tracking-[-0.005em] whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "bg-foreground/[0.06] text-foreground/70",
        success: "bg-success/10 text-success",
        warning: "bg-warning/12 text-warning",
        danger: "bg-destructive/10 text-destructive",
        accent: "bg-accent/10 text-accent",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export type Tone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

export function Badge({
  className,
  tone,
  ...props
}: ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

export function Dot({ tone, pulse }: { tone: Tone; pulse?: boolean }) {
  const color = {
    neutral: "bg-foreground/35",
    success: "bg-success",
    warning: "bg-warning",
    danger: "bg-destructive",
    accent: "bg-accent",
  }[tone];
  return (
    <span aria-hidden className="relative flex size-1.5">
      {pulse && (
        <span className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-60", color)} />
      )}
      <span className={cn("relative inline-flex size-1.5 rounded-full", color)} />
    </span>
  );
}
