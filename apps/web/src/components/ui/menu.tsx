"use client";

import { Menu as BaseMenu } from "@base-ui-components/react/menu";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";

export function Menu({
  trigger,
  triggerClassName,
  triggerLabel,
  children,
  align = "start",
  popupClassName,
}: {
  trigger: ReactNode;
  triggerClassName?: string;
  triggerLabel?: string;
  children: ReactNode;
  align?: "start" | "end" | "center";
  popupClassName?: string;
}) {
  return (
    <BaseMenu.Root>
      <BaseMenu.Trigger aria-label={triggerLabel} className={triggerClassName}>
        {trigger}
      </BaseMenu.Trigger>
      <BaseMenu.Portal>
        <BaseMenu.Positioner sideOffset={6} align={align} className="z-50 outline-none">
          <BaseMenu.Popup
            className={cn(
              "min-w-56 origin-[var(--transform-origin)] rounded-2xl border border-border bg-background p-1.5 shadow-lg outline-none transition-[opacity,transform] duration-150 ease-out data-[ending-style]:scale-95 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0",
              popupClassName,
            )}
          >
            {children}
          </BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>
  );
}

export function MenuItem({ className, ...props }: ComponentProps<typeof BaseMenu.Item>) {
  return (
    <BaseMenu.Item
      className={cn(
        "flex min-h-9 cursor-pointer items-center gap-2.5 rounded-lg px-2.5 text-[13px] text-foreground/80 outline-none transition-colors duration-100 data-[highlighted]:bg-muted data-[highlighted]:text-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <p className="eyebrow px-2.5 pb-1 pt-2 text-[10px]">{children}</p>;
}

export function MenuSeparator() {
  return <BaseMenu.Separator className="-mx-1.5 my-1.5 h-px bg-border" />;
}
