// Response shapes of the FastAPI app, derived from its OpenAPI schema (src/lib/api-schema.ts, written
// by `npm run gen:api`). Only the names components import live here; never hand-write a shape.
import type { components } from "./api-schema";

type Schemas = components["schemas"];

export type Severity = keyof Schemas["SeverityCounts"];
export type ActionStatus = Schemas["ActionPatch"]["status"];

export type Service = Schemas["ServiceOut"];
export type GraphNode = Schemas["GraphNode"];
export type GraphEdge = Schemas["GraphEdge"];
export type ServiceGraph = Schemas["GraphOut"];
export type PrunablePackage = Schemas["PrunableOut"];
export type Vulnerability = Schemas["VulnOut"];
export type VulnerabilityPage = Schemas["Page_VulnOut_"];
export type RemediationStep = Schemas["PlanStep"];
export type PlanAction = Schemas["ActionOut"];
export type RemediationPlan = Schemas["PlanOut"];
export type MeTeam = Schemas["MeTeam"];
export type Me = Schemas["MeOut"];
export type StatsOverview = Schemas["StatsOut"];
export type Cluster = Schemas["ClusterOut"];
export type ClusterMember = Schemas["ClusterMember"];
export type ClusterRedundancy = Schemas["RedundancyOut"];
