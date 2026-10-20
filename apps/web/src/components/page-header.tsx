import type { ReactNode } from "react";

export function PageHeader({
  overline,
  title,
  description,
  actions,
  badge,
}: {
  overline: string;
  title: string;
  description?: string;
  actions?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-x-6 gap-y-4 sm:mb-10">
      <div className="min-w-0 animate-fade-up">
        <p className="eyebrow mb-3">{overline}</p>
        <h1 className="text-[34px] font-semibold leading-[1.05] tracking-[-0.055em] sm:text-[44px]">{title}</h1>
        {description && <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-foreground/55">{description}</p>}
      </div>
      {(actions || badge) && (
        <div className="flex flex-wrap items-center gap-3">
          {badge}
          {actions}
        </div>
      )}
    </header>
  );
}
