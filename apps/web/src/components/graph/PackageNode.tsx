import { Handle, Position, type NodeProps } from "reactflow";
import { sevColor } from "@/lib/severity";
import type { GraphNode, Severity } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface PackageNodeData extends GraphNode {
  severity: Severity | null; // worst vulnerability on this package itself
  selected: boolean;
  onPath: boolean; // an ancestor of the selected package
}

const TINT: Record<Severity, string> = {
  CRITICAL: "border-crit/60 bg-crit/10",
  HIGH: "border-high/60 bg-high/10",
  MEDIUM: "border-med/60 bg-med/10",
  LOW: "border-low/60 bg-low/10",
};

const hidden = "!h-1 !w-1 !min-w-0 !border-0 !bg-transparent";

export function PackageNode({ data }: NodeProps<PackageNodeData>) {
  return (
    <div
      className={cn(
        "w-[208px] rounded-lg border px-3 py-1.5 transition-colors",
        data.severity ? TINT[data.severity] : "border-line-strong bg-card",
        data.onPath && "border-accent",
        data.selected && "ring-2 ring-accent",
      )}
    >
      <Handle type="target" position={Position.Left} className={hidden} />
      <div className="truncate text-[13px] font-medium leading-4">{data.name}</div>
      <div className="mt-1 flex items-center justify-between gap-2 font-mono text-xs leading-4 text-fg3">
        <span className="truncate">{data.version}</span>
        {data.severity && (
          <span className="flex shrink-0 items-center gap-1.5 text-fg2">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: sevColor(data.severity) }} />
            {data.max_cvss?.toFixed(1)}
          </span>
        )}
      </div>
      <Handle type="source" position={Position.Right} className={hidden} />
    </div>
  );
}
