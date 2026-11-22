"use client";

import { Maximize, ZoomIn, ZoomOut } from "lucide-react";
import { useReactFlow } from "reactflow";
import { Button } from "@/components/ui/button";
import { SEVERITIES, sevColor } from "@/lib/severity";

export function GraphControls() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <div className="flex items-center gap-2" role="toolbar" aria-label="Graph controls">
        <Button variant="outline" size="icon" aria-label="Zoom in" onClick={() => zoomIn()}>
          <ZoomIn aria-hidden strokeWidth={1.5} className="h-4 w-4" />
        </Button>
        <Button variant="outline" size="icon" aria-label="Zoom out" onClick={() => zoomOut()}>
          <ZoomOut aria-hidden strokeWidth={1.5} className="h-4 w-4" />
        </Button>
        <Button variant="outline" size="icon" aria-label="Fit view" onClick={() => fitView({ padding: 0.1 })}>
          <Maximize aria-hidden strokeWidth={1.5} className="h-4 w-4" />
        </Button>
      </div>
      <ul aria-label="Legend" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg2">
        {SEVERITIES.map((s) => (
          <li key={s} className="flex items-center gap-1.5">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: sevColor(s) }} />
            {s[0] + s.slice(1).toLowerCase()}
          </li>
        ))}
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="h-0.5 w-4 rounded-full bg-accent" />
          Selected path
        </li>
      </ul>
    </div>
  );
}
