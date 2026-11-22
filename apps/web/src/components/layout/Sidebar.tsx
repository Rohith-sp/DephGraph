"use client";

import { Boxes, GitFork, LayoutDashboard, Network, Settings, ShieldAlert, Wrench } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/services", label: "Services", icon: Boxes },
  { href: "/vulnerabilities", label: "Vulnerabilities", icon: ShieldAlert },
  { href: "/remediation", label: "Remediation", icon: Wrench },
  { href: "/clusters", label: "Clusters", icon: Network },
  { href: "/settings", label: "Settings", icon: Settings },
];

// A column on desktop; a scrolling row under the brand on narrow screens.
export function Sidebar() {
  const path = usePathname();
  return (
    <nav
      aria-label="Main"
      className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-line bg-side p-3 md:sticky md:top-0 md:h-screen md:w-60 md:flex-col md:items-stretch md:gap-0.5 md:overflow-visible md:border-b-0 md:border-r md:p-4"
    >
      <div className="flex h-9 shrink-0 items-center gap-2.5 px-2.5 text-base font-medium md:mb-6">
        <GitFork aria-hidden strokeWidth={1.5} className="h-[18px] w-[18px]" /> DephGraph
      </div>
      {LINKS.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex h-9 shrink-0 items-center gap-2.5 rounded-lg px-2.5 transition-colors",
              active
                ? "bg-raised text-fg shadow-[inset_0_1px_0_rgb(var(--fg)/0.08)]"
                : "text-fg2 hover:bg-raised/60 hover:text-fg",
            )}
          >
            <Icon aria-hidden strokeWidth={1.5} className={cn("h-4 w-4", active ? "text-accent" : "text-fg3")} />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
