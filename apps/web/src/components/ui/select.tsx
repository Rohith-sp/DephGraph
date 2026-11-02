import * as React from "react";
import { cn } from "@/lib/utils";

// shortcut: native <select> (keyboard/a11y for free), not Radix; swap if custom popovers are needed.
export const Select = ({ className, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) => (
  <select
    className={cn(
      "h-9 rounded-md border border-slate-300 bg-white px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400",
      className,
    )}
    {...p}
  />
);
