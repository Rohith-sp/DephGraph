"use client";

import { useMemo, useState } from "react";
import ReactFlow, { Background, ReactFlowProvider, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import { Button } from "@/components/ui/button";
import { sevColor, severityFromCvss } from "@/lib/severity";
import type { GraphNode, ServiceGraph } from "@/lib/types";
import { cn } from "@/lib/utils";
import { GraphControls } from "./GraphControls";
import { PackageNode, type PackageNodeData } from "./PackageNode";
import { PathChain } from "./PathChain";

const nodeTypes = { package: PackageNode };
const COL_W = 288;
const ROW_H = 52;
const MAX_PATHS = 5;

const mapOf = (edges: ServiceGraph["edges"], from: "source" | "target", to: "source" | "target") => {
  const m = new Map<string, string[]>();
  edges.forEach((e) => m.set(e[from], [...(m.get(e[from]) ?? []), e[to]]));
  return m;
};

// Column = depth. Within a column rows follow the average row of their parents, which keeps
// edges short; short columns are centred against the tallest one.
function layout(nodes: GraphNode[], parents: Map<string, string[]>) {
  const cols = new Map<number, GraphNode[]>();
  nodes.forEach((n) => cols.set(n.depth, [...(cols.get(n.depth) ?? []), n]));
  const y = new Map<string, number>();
  const pos = new Map<string, { x: number; y: number }>();
  const tallest = Math.max(...[...cols.values()].map((c) => c.length));
  [...cols.keys()]
    .sort((a, b) => a - b)
    .forEach((d) => {
      const bary = (n: GraphNode) => {
        const ys = (parents.get(n.id) ?? []).flatMap((p) => (y.has(p) ? [y.get(p)!] : []));
        return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : 0;
      };
      const col = cols.get(d)!.sort((a, b) => bary(a) - bary(b) || a.name.localeCompare(b.name));
      const offset = ((tallest - col.length) * ROW_H) / 2;
      col.forEach((n, i) => {
        y.set(n.id, offset + i * ROW_H);
        pos.set(n.id, { x: d * COL_W, y: offset + i * ROW_H });
      });
    });
  return pos;
}

export function DependencyGraph({ graph }: { graph: ServiceGraph }) {
  const model = useMemo(() => {
    const byId = new Map(graph.nodes.map((n) => [n.id, n]));
    const parents = mapOf(graph.edges, "target", "source");
    const children = mapOf(graph.edges, "source", "target");
    // Worst CVSS at or below each node: edges leading toward a vulnerable package get tinted.
    const below = new Map<string, number | null>();
    const worst = (id: string, seen = new Set<string>()): number | null => {
      if (below.has(id)) return below.get(id)!;
      if (seen.has(id)) return null;
      seen.add(id);
      const own = byId.get(id)?.vuln_count ? (byId.get(id)!.max_cvss ?? 0) : null;
      const all = [own, ...(children.get(id) ?? []).map((c) => worst(c, seen))].filter((v): v is number => v !== null);
      const r = all.length ? Math.max(...all) : null;
      below.set(id, r);
      return r;
    };
    graph.nodes.forEach((n) => worst(n.id));
    const vulnerable = graph.nodes
      .filter((n) => n.vuln_count > 0)
      .sort((a, b) => (b.max_cvss ?? 0) - (a.max_cvss ?? 0) || a.name.localeCompare(b.name));
    return { byId, parents, below, vulnerable, pos: layout(graph.nodes, parents) };
  }, [graph]);

  const [selected, setSelected] = useState<string | null>(model.vulnerable[0]?.id ?? null);

  // Every ancestor of the selection (plus itself): the edges between them are the path being read.
  const trail = useMemo(() => {
    const seen = new Set<string>();
    const walk = (id: string) => {
      if (seen.has(id)) return;
      seen.add(id);
      (model.parents.get(id) ?? []).forEach(walk);
    };
    if (selected) walk(selected);
    return seen;
  }, [model, selected]);

  const nodes = useMemo<Node<PackageNodeData>[]>(
    () =>
      graph.nodes.map((n) => ({
        id: n.id,
        type: "package",
        position: model.pos.get(n.id)!,
        data: {
          ...n,
          severity: n.vuln_count > 0 ? severityFromCvss(n.max_cvss ?? 0) : null,
          selected: n.id === selected,
          onPath: trail.has(n.id) && n.id !== selected,
        },
      })),
    [graph.nodes, model, selected, trail],
  );

  const edges = useMemo<Edge[]>(
    () =>
      graph.edges.map((e) => {
        const risk = model.below.get(e.target) ?? null;
        const onTrail = trail.has(e.target) && trail.has(e.source);
        const stroke = onTrail
          ? "rgb(var(--accent))"
          : risk !== null
            ? sevColor(severityFromCvss(risk), 0.7)
            : "rgb(var(--fg) / 0.12)";
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          zIndex: onTrail ? 2 : risk !== null ? 1 : 0,
          style: { stroke, strokeWidth: onTrail ? 2 : risk !== null ? 1.5 : 1 },
        };
      }),
    [graph.edges, model, trail],
  );

  // Up to MAX_PATHS chains from a direct dependency down to the selection.
  const paths = useMemo(() => {
    const out: GraphNode[][] = [];
    const walk = (id: string, tail: GraphNode[]) => {
      if (out.length >= MAX_PATHS || tail.length > 12) return;
      const chain = [model.byId.get(id)!, ...tail];
      const ps = model.parents.get(id) ?? [];
      if (ps.length === 0) out.push(chain);
      else ps.forEach((p) => walk(p, chain));
    };
    if (selected) walk(selected, []);
    return out;
  }, [model, selected]);

  if (graph.nodes.length === 0) return <p className="text-fg2">No dependencies resolved for this service.</p>;
  const sel = selected ? model.byId.get(selected) : undefined;

  return (
    <div className="space-y-4">
      <ReactFlowProvider>
        <GraphControls />
        <div
          className="h-[80vh] min-h-[480px] overflow-hidden rounded-xl border border-line bg-card"
          aria-label="Dependency graph"
        >
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.1 }}
            minZoom={0.1}
            nodesConnectable={false}
            nodesDraggable={false}
            nodesFocusable={false}
            edgesFocusable={false}
            onNodeClick={(_, n) => setSelected(n.id)}
            onPaneClick={() => setSelected(null)}
          >
            <Background color="rgb(var(--fg) / 0.1)" gap={24} size={1} />
          </ReactFlow>
        </div>
      </ReactFlowProvider>

      <section aria-label="Package details" className="space-y-4 rounded-xl border border-line bg-card p-5">
        {model.vulnerable.length > 0 && (
          <div className="space-y-2">
            <h2 className="text-sm font-normal text-fg2">Vulnerable packages</h2>
            <ul className="flex flex-wrap gap-2">
              {model.vulnerable.map((n) => (
                <li key={n.id}>
                  <Button
                    size="sm"
                    variant="outline"
                    aria-pressed={n.id === selected}
                    onClick={() => setSelected(n.id)}
                    className={cn("font-mono text-xs", n.id === selected && "border-accent")}
                  >
                    <span
                      aria-hidden
                      className="h-1.5 w-1.5 rounded-full"
                      style={{ background: sevColor(severityFromCvss(n.max_cvss ?? 0)) }}
                    />
                    {n.name} {n.version}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {sel ? (
          <div className="space-y-2">
            <p>
              <span className="font-medium">{sel.name}</span>{" "}
              <span className="font-mono text-xs text-fg2">{sel.version}</span>
            </p>
            <p className="text-fg2">
              {sel.ecosystem} · {sel.is_direct ? "direct dependency" : `transitive, depth ${sel.depth}`}
              {sel.vuln_count > 0 &&
                ` · ${sel.vuln_count} ${sel.vuln_count === 1 ? "vulnerability" : "vulnerabilities"}, max CVSS ${sel.max_cvss?.toFixed(1)}`}
            </p>
            <ul aria-label={`Paths to ${sel.name}`} className="space-y-1.5 pt-1">
              {paths.map((p, i) => (
                <li key={i}>
                  <PathChain steps={[{ name: graph.service.name }, ...p.map((n) => ({ name: n.name, version: n.version }))]} />
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="text-fg2">Select a package to see how this service reaches it.</p>
        )}
      </section>
    </div>
  );
}
