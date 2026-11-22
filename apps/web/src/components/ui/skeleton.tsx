import { cn } from "@/lib/utils";

export const Skeleton = ({ className }: { className?: string }) => (
  <div aria-hidden className={cn("rounded-xl bg-raised", className)} />
);
