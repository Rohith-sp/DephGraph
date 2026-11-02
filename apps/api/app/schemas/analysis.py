from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ServiceOut(BaseModel):
    service_id: int
    team_id: int
    team_name: str
    name: str
    language: str | None
    repo_url: str | None
    package_count: int
    vuln_count: int
    critical_count: int


class GraphService(BaseModel):
    service_id: int
    name: str


class GraphNode(BaseModel):
    id: str
    package_id: int
    name: str
    ecosystem: str
    version: str
    is_direct: bool
    depth: int
    vuln_count: int
    max_cvss: float | None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str


class GraphOut(BaseModel):
    service: GraphService
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class PrunableOut(BaseModel):
    package_id: int
    name: str
    ecosystem: str
    version: str
    exec_count: int
    deploy_seq: int


class VulnRef(BaseModel):
    vuln_id: int
    osv_id: str
    severity: str | None
    cvss_score: float | None


class VulnerablePackage(BaseModel):
    package_id: int
    name: str
    version: str
    depth: int
    path: list[str]
    vulns: list[VulnRef]


class BlastRadiusOut(BaseModel):
    service_id: int
    vulnerable_packages: list[VulnerablePackage]
    total_vulns: int
    critical_count: int
    cached: bool = False


class VulnOut(BaseModel):
    vuln_id: int
    osv_id: str
    title: str | None
    severity: str | None
    cvss_score: float | None
    published_at: datetime | None
    affected_packages: int
    affected_services: int
    has_fix: bool


class AffectedOut(BaseModel):
    package_id: int
    name: str
    ecosystem: str
    version: str
    fixed_in: str | None


class VulnDetail(VulnOut):
    summary: str | None
    details: str | None
    affected: list[AffectedOut]


class PlanStep(BaseModel):
    step: int
    package: str
    package_id: int
    ecosystem: str
    from_version: str
    to_version: str
    vulns_fixed: list[str]
    services_affected: list[str]
    new_coverage: int
    cumulative_coverage_pct: float


class PlanCreate(BaseModel):
    team_id: int
    title: str | None = Field(default=None, max_length=255)


class ActionOut(BaseModel):
    action_seq: int
    description: str | None
    status: str


class PlanOut(BaseModel):
    plan_id: int
    team_id: int
    title: str | None
    status: str
    coverage_pct: float
    actions: list[ActionOut]


class ActionPatch(BaseModel):
    status: Literal["DONE", "SKIPPED", "PENDING"]


class SeverityCounts(BaseModel):
    CRITICAL: int = 0
    HIGH: int = 0
    MEDIUM: int = 0
    LOW: int = 0


class StatsOut(BaseModel):
    services: int
    packages: int
    open_vulns: int
    critical_vulns: int
    prunable_packages: int
    copyleft_packages: int
    severity_counts: SeverityCounts


class SyncOut(BaseModel):
    packages_scanned: int
    vulns_fetched: int


class ClusterOut(BaseModel):
    cluster_id: int
    label: str
    keywords: list[str]
    member_count: int


class ClusterMember(BaseModel):
    package_id: int
    name: str
    ecosystem: str
    x: float | None  # null only if Mongo embeddings are unavailable
    y: float | None


class RedundancyOut(BaseModel):
    service_id: int
    service_name: str
    cluster_id: int
    cluster_label: str
    package_count: int
    packages: list[str]


class VerdictOut(BaseModel):
    pr_number: int
    dep_name: str
    verdict: Literal["PASS", "ADVISORY", "BLOCK"]
    stage_reached: int
    reasoning: str | None
    decided_at: datetime
