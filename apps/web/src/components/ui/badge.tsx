import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const dot = cva("h-1.5 w-1.5 shrink-0 rounded-full", {
  variants: {
    variant: { default: "bg-fg3", critical: "bg-crit", high: "bg-high", medium: "bg-med", low: "bg-low" },
  },
  defaultVariants: { variant: "default" },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof dot> {}

// Severity is a status dot plus a mono code, like a log level; the colour never carries the text.
export const Badge = ({ className, variant, children, ...p }: BadgeProps) => (
  <span className={cn("inline-flex items-center gap-1.5 font-mono text-xs text-fg2", className)} {...p}>
    <span aria-hidden className={dot({ variant })} />
    {children}
  </span>
);
