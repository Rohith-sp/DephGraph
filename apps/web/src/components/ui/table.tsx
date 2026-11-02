import * as React from "react";
import { cn } from "@/lib/utils";

export const Table = ({ className, ...p }: React.TableHTMLAttributes<HTMLTableElement>) => (
  <div className="w-full overflow-auto">
    <table className={cn("w-full text-left text-sm", className)} {...p} />
  </div>
);
export const THead = (p: React.HTMLAttributes<HTMLTableSectionElement>) => (
  <thead className="border-b border-slate-200 text-slate-500" {...p} />
);
export const TBody = (p: React.HTMLAttributes<HTMLTableSectionElement>) => <tbody {...p} />;
export const TR = ({ className, ...p }: React.HTMLAttributes<HTMLTableRowElement>) => (
  <tr className={cn("border-b border-slate-100 last:border-0", className)} {...p} />
);
export const TH = ({ className, ...p }: React.ThHTMLAttributes<HTMLTableCellElement>) => (
  <th scope="col" className={cn("px-3 py-2 font-medium", className)} {...p} />
);
export const TD = ({ className, ...p }: React.TdHTMLAttributes<HTMLTableCellElement>) => (
  <td className={cn("px-3 py-2", className)} {...p} />
);
