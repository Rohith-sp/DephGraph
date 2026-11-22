import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = ({ className, ...p }: React.InputHTMLAttributes<HTMLInputElement>) => (
  <input
    className={cn(
      "h-9 w-full rounded-lg border border-line-strong bg-card px-3 text-sm placeholder:text-fg3",
      className,
    )}
    {...p}
  />
);
