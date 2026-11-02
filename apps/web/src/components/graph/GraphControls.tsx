"use client";

import { Maximize, ZoomIn, ZoomOut } from "lucide-react";
import { useReactFlow } from "reactflow";
import { Button } from "@/components/ui/button";

export function GraphControls() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  return (
    <div className="flex items-center gap-2" role="toolbar" aria-label="Graph controls">
      <Button variant="outline" size="icon" aria-label="Zoom in" onClick={() => zoomIn()}>
        <ZoomIn aria-hidden className="h-4 w-4" />
      </Button>
      <Button variant="outline" size="icon" aria-label="Zoom out" onClick={() => zoomOut()}>
        <ZoomOut aria-hidden className="h-4 w-4" />
      </Button>
      <Button variant="outline" size="icon" aria-label="Fit view" onClick={() => fitView({ padding: 0.2 })}>
        <Maximize aria-hidden className="h-4 w-4" />
      </Button>
      <span className="ml-2 flex items-center gap-1 text-xs text-slate-600">
        <span aria-hidden className="inline-block h-3 w-3 rounded border-2 border-red-600 bg-red-50" /> vulnerable
      </span>
    </div>
  );
}
