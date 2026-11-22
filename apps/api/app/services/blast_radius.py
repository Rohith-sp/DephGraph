"""Transitive vulnerability exposure per service: closure from resolves_to/depends_on, BFS paths
(the v_blast_radius view is not queried here), cached through app.services.cache."""

import json
from collections import deque
from collections.abc import Collection
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import cache
from app.services.queries import Rows, latest_seq, rows

NODES_SQL = """
        SELECT rt.package_id, p.name, p.ecosystem, rt.version_string AS version, rt.depth,
               rt.is_direct,
               (SELECT count(*) FROM affects a WHERE (a.package_id, a.version_string)
                  = (rt.package_id, rt.version_string)) AS vuln_count,
               (SELECT max(v.cvss_score) FROM affects a JOIN vulnerability v USING (vuln_id)
                WHERE (a.package_id, a.version_string) = (rt.package_id, rt.version_string)
               ) AS max_cvss
        FROM resolves_to rt JOIN package p ON p.package_id = rt.package_id
        WHERE rt.service_id = :s AND rt.deploy_seq = :q
        ORDER BY rt.depth, p.name
        """
# depends_on edges count only for the ecosystems the deployment took from manifests
# (deployment.manifest_ecosystems; every writer of a deployment must set it).
EDGES_SQL = """
        SELECT d.dep_package_id, d.dep_version_string, d.ref_package_id, d.ref_version_string
        FROM depends_on d
        JOIN deployment dp ON dp.service_id = :s AND dp.deploy_seq = :q
        JOIN package dpk ON dpk.package_id = d.dep_package_id
                        AND dpk.ecosystem = ANY(dp.manifest_ecosystems)
        JOIN resolves_to a ON (a.package_id, a.version_string)
                            = (d.dep_package_id, d.dep_version_string)
                          AND a.service_id = :s AND a.deploy_seq = :q
        JOIN resolves_to b ON (b.package_id, b.version_string)
                            = (d.ref_package_id, d.ref_version_string)
                          AND b.service_id = :s AND b.deploy_seq = :q
        UNION
        SELECT e.dep_package_id, e.dep_version_string, e.ref_package_id, e.ref_version_string
        FROM deployment_edge e
        WHERE e.service_id = :s AND e.deploy_seq = :q
        """


async def load_closure(session: AsyncSession, service_id: int, seq: int) -> tuple[Rows, Rows]:
    """(nodes, edges) of the service's dependency closure at deployment `seq`."""
    nodes = await rows(session, NODES_SQL, s=service_id, q=seq)
    edges = await rows(session, EDGES_SQL, s=service_id, q=seq)
    return nodes, edges


async def closure_truncated(session: AsyncSession, service_id: int, seq: int) -> bool:
    """True when the deployment's transitive closure stopped at the depth cap (partial graph)."""
    found = await rows(
        session,
        "SELECT closure_truncated FROM deployment WHERE service_id = :s AND deploy_seq = :q",
        s=service_id,
        q=seq,
    )
    return bool(found and found[0]["closure_truncated"])


def shortest_paths(nodes: Rows, edges: Rows) -> dict[tuple[int, str], list[str]]:
    """BFS from the direct dependencies: node -> ['direct@v', ..., 'node@v']."""
    label = {(n["package_id"], n["version"]): f"{n['name']}@{n['version']}" for n in nodes}
    adj: dict[tuple[int, str], list[tuple[int, str]]] = {}
    for e in edges:
        adj.setdefault((e["dep_package_id"], e["dep_version_string"]), []).append(
            (e["ref_package_id"], e["ref_version_string"])
        )
    paths = {
        (n["package_id"], n["version"]): [label[(n["package_id"], n["version"])]]
        for n in nodes
        if n["is_direct"]
    }
    queue = deque(paths)
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, []):
            if nxt not in paths:
                paths[nxt] = [*paths[cur], label[nxt]]
                queue.append(nxt)
    return paths


async def compute(session: AsyncSession, service_id: int) -> dict[str, Any]:
    seq = await latest_seq(session, service_id)
    out: dict[str, Any] = {
        "service_id": service_id,
        "vulnerable_packages": [],
        "total_vulns": 0,
        "critical_count": 0,
        "truncated": False,
        "cached": False,
    }
    if seq is None:
        return out
    nodes, edges = await load_closure(session, service_id, seq)
    out["truncated"] = await closure_truncated(session, service_id, seq)
    paths = shortest_paths(nodes, edges)
    hits = await rows(
        session,
        """
        SELECT rt.package_id, rt.version_string AS version, v.vuln_id, v.osv_id, v.severity,
               v.cvss_score
        FROM resolves_to rt
        JOIN affects a ON (a.package_id, a.version_string) = (rt.package_id, rt.version_string)
        JOIN vulnerability v ON v.vuln_id = a.vuln_id
        WHERE rt.service_id = :s AND rt.deploy_seq = :q
        ORDER BY v.cvss_score DESC NULLS LAST, v.vuln_id
        """,
        s=service_id,
        q=seq,
    )
    by_node = {(n["package_id"], n["version"]): n for n in nodes}
    pkgs: dict[tuple[int, str], dict[str, Any]] = {}
    for h in hits:
        key = (h["package_id"], h["version"])
        n = by_node[key]
        entry = pkgs.setdefault(
            key,
            {
                "package_id": n["package_id"],
                "name": n["name"],
                "version": n["version"],
                "depth": n["depth"],
                "path": paths.get(key, [f"{n['name']}@{n['version']}"]),
                "vulns": [],
            },
        )
        entry["vulns"].append(
            {
                "vuln_id": h["vuln_id"],
                "osv_id": h["osv_id"],
                "severity": h["severity"],
                "cvss_score": float(h["cvss_score"]) if h["cvss_score"] is not None else None,
            }
        )
    out["vulnerable_packages"] = sorted(pkgs.values(), key=lambda p: (p["depth"], p["name"]))
    uniq = {v["vuln_id"]: v for p in pkgs.values() for v in p["vulns"]}
    out["total_vulns"] = len(uniq)
    out["critical_count"] = sum(v["severity"] == "CRITICAL" for v in uniq.values())
    return out


async def get_blast_radius(
    session: AsyncSession, service_id: int, teams: Collection[int]
) -> dict[str, Any]:
    async def build() -> str:
        return json.dumps(await compute(session, service_id))

    body, hit = await cache.read_through(f"blast_radius:{service_id}", build, teams)
    return {**json.loads(body), "cached": hit}
