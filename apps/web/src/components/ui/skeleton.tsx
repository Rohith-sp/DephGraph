import { cn } from "@/lib/utils";

export const Skeleton = ({ className }: { className?: string }) => (
  <div aria-hidden className={cn("animate-pulse rounded-md bg-slate-200", className)} />
);
