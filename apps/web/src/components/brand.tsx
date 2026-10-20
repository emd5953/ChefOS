import Image from "next/image";
import Link from "next/link";
import { cn } from "@/lib/cn";

export function Brand({ href = "/app", className }: { href?: string; className?: string }) {
  const content = (
    <>
      <Image src="/assets/logo.png" alt="" width={28} height={28} className="size-7 object-contain" />
      <span>ChefOS</span>
    </>
  );
  const cls = cn(
    "inline-flex items-center gap-2 text-[19px] font-semibold tracking-[-0.055em] transition-transform duration-150 active:scale-[0.97]",
    className,
  );
  // /app links are in-app; "/" is the static marketing site, so use a plain anchor.
  return href === "/" ? (
    <a href="/" className={cls} aria-label="ChefOS home">
      {content}
    </a>
  ) : (
    <Link href={href} className={cls} aria-label="ChefOS home">
      {content}
    </Link>
  );
}
