"use client";

import { useMemo } from "react";
import ReactFlow, { Background, ReactFlowProvider, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import type { GraphNode, ServiceGraph } from "@/lib/types";
import { GraphControls } from "./GraphControls";
import { PackageNode } from "./PackageNode";
import { VulnNode } from "./VulnNode";

const nodeTypes = { package: PackageNode, vuln: VulnNode };
const COL_W = 240;
const ROW_H = 80;

// Layered layout: column = depth, row = order within the column.
function layout(nodes: GraphNode[]): Node<GraphNode>[] {
  const rows = new Map<number, number>();
  return nodes.map((n) => {
    const row = rows.get(n.depth) ?? 0;
    rows.set(n.depth, row + 1);
    return {
      id: n.id,
      type: n.vuln_count > 0 ? "vuln" : "package",
      position: { x: n.depth * COL_W, y: row * ROW_H },
      data: n,
    };
  });
}

export function DependencyGraph({ graph }: { graph: ServiceGraph }) {
  const nodes = useMemo(() => layout(graph.nodes), [graph.nodes]);
  const edges = useMemo<Edge[]>(
    () => graph.edges.map((e) => ({ id: e.id, source: e.source, target: e.target })),
    [graph.edges],
  );
  if (nodes.length === 0) return <p className="text-sm text-slate-500">No dependencies resolved for this service.</p>;
  return (
    <ReactFlowProvider>
      <div className="space-y-2">
        <GraphControls />
        <div className="h-[600px] rounded-lg border border-slate-200 bg-white" aria-label="Dependency graph">
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView nodesConnectable={false}>
            <Background />
          </ReactFlow>
        </div>
      </div>
    </ReactFlowProvider>
  );
}
