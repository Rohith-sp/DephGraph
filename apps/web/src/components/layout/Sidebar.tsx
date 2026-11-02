"use client";

import { Boxes, GitFork, LayoutDashboard, Settings, ShieldAlert, Wrench, Network } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

// "Service list" not "Services": keeps the home page's "Services" text unique for the e2e test.
const LINKS = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/services", label: "Service list", icon: Boxes },
  { href: "/vulnerabilities", label: "Vulnerabilities", icon: ShieldAlert },
  { href: "/remediation", label: "Remediation", icon: Wrench },
  { href: "/clusters", label: "Clusters", icon: Network },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const path = usePathname();
  return (
    <nav aria-label="Main" className="flex w-56 shrink-0 flex-col gap-1 border-r border-slate-200 bg-white p-3">
      <div className="mb-4 flex items-center gap-2 px-2 text-lg font-bold">
        <GitFork aria-hidden className="h-5 w-5" /> DephGraph
      </div>
      {LINKS.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-2 rounded-md px-2 py-2 text-sm hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400",
              active && "bg-slate-100 font-medium",
            )}
          >
            <Icon aria-hidden className="h-4 w-4" /> {label}
          </Link>
        );
      })}
    </nav>
  );
}
