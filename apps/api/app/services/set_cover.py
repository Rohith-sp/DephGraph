"""Greedy set cover (Chvatal 1979: H(n) ~ ln(n)+1 approximation).

remediation.build_plan feeds disjoint sets (one element per service/package/vuln), where greedy
reduces to ranking packages by open coverage; overlapping sets get the full H(n) guarantee."""

from typing import Any


def greedy_set_cover(
    open_vulns: set[Any], pkg_coverage: dict[Any, set[Any]]
) -> list[dict[str, Any]]:
    """Pick the package fixing the most still-open elements (any hashable, e.g. vuln ids or
    (service, package, vuln) tuples) until none can be added.

    Ties break by sorted key order (keys must be mutually orderable) for determinism. Vulns no package covers stay uncovered, so
    the final cumulative_coverage_pct can be < 100.
    """
    remaining = set(open_vulns)
    total = len(open_vulns)
    cov = {k: v & open_vulns for k, v in pkg_coverage.items()}
    plan: list[dict[str, Any]] = []
    while remaining and cov:
        best = max(sorted(cov), key=lambda k: len(cov[k] & remaining))
        gain = cov[best] & remaining
        if not gain:
            break
        remaining -= gain
        plan.append(
            {
                "step": len(plan) + 1,
                "package": best,
                "vulns_fixed": sorted(gain),
                "new_coverage": len(gain),
                "cumulative_coverage_pct": round(100 * (total - len(remaining)) / total, 2),
            }
        )
    return plan
