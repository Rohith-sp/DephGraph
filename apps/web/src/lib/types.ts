// shortcut: hand-written from the API contract, no `supabase gen types` (needs a Supabase project).
// Regenerate DB types once a project exists; these are API response shapes, not DB rows.

export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type ActionStatus = "DONE" | "SKIPPED" | "PENDING";

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Service {
  service_id: number;
  team_id: number;
  team_name: string;
  name: string;
  language: string | null;
  repo_url: string | null;
  package_count: number;
  vuln_count: number;
  critical_count: number;
}

export interface GraphNode {
  id: string;
  package_id: number;
  name: string;
  ecosystem: string;
  version: string;
  is_direct: boolean;
  depth: number;
  vuln_count: number;
  max_cvss: number | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
}

export interface ServiceGraph {
  service: { service_id: number; name: string };
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface PrunablePackage {
  package_id: number;
  name: string;
  ecosystem: string;
  version: string;
  exec_count: number;
  deploy_seq: number;
}

export interface Vulnerability {
  vuln_id: number;
  osv_id: string;
  title: string | null;
  severity: Severity | "NONE" | null;
  cvss_score: number | null;
  published_at: string | null;
  affected_packages: number;
  affected_services: number;
  has_fix: boolean;
}

export interface RemediationStep {
  step: number;
  package: string;
  package_id: number;
  ecosystem: string;
  from_version: string;
  to_version: string;
  vulns_fixed: string[];
  services_affected: string[];
  new_coverage: number;
  cumulative_coverage_pct: number;
}

export interface PlanAction {
  action_seq: number;
  description: string;
  status: ActionStatus;
}

export interface RemediationPlan {
  plan_id: number;
  team_id: number;
  title: string;
  status: string;
  coverage_pct: number;
  actions: PlanAction[];
}

export interface StatsOverview {
  services: number;
  packages: number;
  open_vulns: number;
  critical_vulns: number;
  prunable_packages: number;
  severity_counts: Record<Severity, number>;
}

export interface Cluster {
  cluster_id: number;
  label: string;
  keywords: string[];
  member_count: number;
}

export interface ClusterMember {
  package_id: number;
  name: string;
  ecosystem: string;
  x: number;
  y: number;
}

export interface ClusterRedundancy {
  service_id: number;
  service_name: string;
  cluster_id: number;
  cluster_label: string;
  package_count: number;
  packages: string[];
}
