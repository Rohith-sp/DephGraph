import * as React from "react";
import { cn } from "@/lib/utils";

// Wrapper scrolls sideways on narrow screens so the page itself never does.
export const Table = ({ className, ...p }: React.TableHTMLAttributes<HTMLTableElement>) => (
  <div className="w-full overflow-x-auto rounded-xl border border-line bg-card">
    <table className={cn("w-full text-left text-sm tabular-nums", className)} {...p} />
  </div>
);
export const THead = (p: React.HTMLAttributes<HTMLTableSectionElement>) => (
  <thead className="border-b border-line text-xs text-fg3 [&_tr]:hover:bg-transparent" {...p} />
);
export const TBody = (p: React.HTMLAttributes<HTMLTableSectionElement>) => <tbody {...p} />;
export const TR = ({ className, ...p }: React.HTMLAttributes<HTMLTableRowElement>) => (
  <tr className={cn("border-b border-line last:border-0 hover:bg-raised/40", className)} {...p} />
);
export const TH = ({ className, ...p }: React.ThHTMLAttributes<HTMLTableCellElement>) => (
  <th scope="col" className={cn("h-10 px-4 font-normal", className)} {...p} />
);
export const TD = ({ className, ...p }: React.TdHTMLAttributes<HTMLTableCellElement>) => (
  <td className={cn("px-4 py-2.5", className)} {...p} />
);
