import { Handle, Position, type NodeProps } from "reactflow";
import type { GraphNode } from "@/lib/types";

export function VulnNode({ data }: NodeProps<GraphNode>) {
  return (
    <div className="rounded-md border-2 border-red-600 bg-red-50 px-3 py-2 text-xs shadow-sm">
      <Handle type="target" position={Position.Left} />
      <div className="font-semibold text-red-900">{data.name}</div>
      <div className="text-red-700">
        {data.version} · {data.vuln_count} vuln{data.vuln_count === 1 ? "" : "s"}
        {data.max_cvss !== null && ` · CVSS ${data.max_cvss}`}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
