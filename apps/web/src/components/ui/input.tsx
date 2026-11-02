import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = ({ className, ...p }: React.InputHTMLAttributes<HTMLInputElement>) => (
  <input
    className={cn(
      "h-9 w-full rounded-md border border-slate-300 bg-white px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400",
      className,
    )}
    {...p}
  />
);
