import type { Severity } from "@/lib/types";

export const ECOSYSTEMS = ["pypi", "npm", "maven", "go", "cargo", "nuget"];

export const SEVERITIES: Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];

// CSS variable per severity (defined in globals.css), so charts and chrome share one palette.
export const sevVar: Record<Severity, string> = {
  CRITICAL: "--crit",
  HIGH: "--high",
  MEDIUM: "--med",
  LOW: "--low",
};
export const sevColor = (s: Severity, alpha = 1) => `rgb(var(${sevVar[s]}) / ${alpha})`;

// CVSS v3 qualitative bands; graph nodes only carry the score.
export const severityFromCvss = (n: number): Severity =>
  n >= 9 ? "CRITICAL" : n >= 7 ? "HIGH" : n >= 4 ? "MEDIUM" : "LOW";
