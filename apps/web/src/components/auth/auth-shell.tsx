import Image from "next/image";
import type { ReactNode } from "react";
import { Brand } from "@/components/brand";

export function AuthShell({
  overline,
  title,
  subtitle,
  children,
  footer,
}: {
  overline: string;
  title: string;
  subtitle: string;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      <main id="main" className="flex flex-col px-5 py-6 sm:px-10 lg:px-16">
        <header>
          <Brand href="/" />
        </header>
        <div className="mx-auto flex w-full max-w-[420px] flex-1 flex-col justify-center py-10">
          <p className="eyebrow mb-4">{overline}</p>
          <h1 className="text-[40px] font-semibold leading-[1.05] tracking-[-0.055em] sm:text-[46px]">{title}</h1>
          <p className="mt-3 text-[15px] leading-relaxed text-foreground/55">{subtitle}</p>
          <div className="mt-8">{children}</div>
          <div className="mt-8 text-[13px] text-foreground/55">{footer}</div>
        </div>
      </main>
      <aside aria-hidden className="relative m-5 hidden overflow-hidden rounded-hero bg-ink lg:block">
        <Image
          src="/assets/hero-guests.png"
          alt=""
          fill
          priority
          sizes="50vw"
          className="object-cover object-[center_45%]"
        />
        <div className="absolute inset-0 bg-gradient-to-r from-black/75 via-black/40 to-black/10" />
        <div className="absolute bottom-12 left-12 right-12 text-white">
          <p className="eyebrow mb-4 !text-white/70">Chefos &mdash; Restaurant operations</p>
          <p className="max-w-md text-[40px] font-semibold leading-[1.05] tracking-[-0.055em]">
            Know how the day is going, while it is going.
          </p>
          <p className="mt-4 max-w-sm text-[15px] leading-relaxed text-white/75">
            Live sales, labor, and menu mix from the POS you already run, in one quiet place.
          </p>
        </div>
      </aside>
    </div>
  );
}
