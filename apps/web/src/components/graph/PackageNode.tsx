import { Handle, Position, type NodeProps } from "reactflow";
import type { GraphNode } from "@/lib/types";

export function PackageNode({ data }: NodeProps<GraphNode>) {
  return (
    <div className="rounded-md border border-slate-300 bg-white px-3 py-2 text-xs shadow-sm">
      <Handle type="target" position={Position.Left} />
      <div className="font-medium">{data.name}</div>
      <div className="text-slate-500">{data.version}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
