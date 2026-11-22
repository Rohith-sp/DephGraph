import * as React from "react";
import { cn } from "@/lib/utils";

// shortcut: native <select> (keyboard/a11y for free), not Radix; swap if custom popovers are needed.
export const Select = ({ className, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) => (
  <select className={cn("h-9 rounded-lg border border-line-strong bg-card px-2 text-sm", className)} {...p} />
);
