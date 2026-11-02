from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from packaging.version import InvalidVersion, Version
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import DbSession
from app.models.sql import RemediationAction, RemediationPlan
from app.schemas.analysis import ActionOut, ActionPatch, PlanCreate, PlanOut, PlanStep
from app.schemas.common import Envelope, ok
from app.services.queries import open_vuln_rows, rows
from app.services.set_cover import greedy_set_cover

router = APIRouter(
    prefix="/remediation", dependencies=[Depends(get_current_user)], tags=["remediation"]
)


def _vkey(v: str) -> tuple[int, Version | str]:
    try:
        return (1, Version(v))
    except InvalidVersion:
        return (0, v)


async def build_plan(session: AsyncSession, team_id: int) -> list[dict[str, Any]]:
    """Greedy set cover over fixable open vulns; one step = upgrade one package."""
    found = await rows(session, "SELECT 1 AS ok FROM team WHERE team_id = :t", t=team_id)
    if not found:
        raise HTTPException(404, "Team not found")
    open_rows = await open_vuln_rows(session, team_id)
    by_pkg: dict[int, list[dict[str, Any]]] = {}
    for r in open_rows:
        if r["fixed_in"]:
            by_pkg.setdefault(r["package_id"], []).append(r)
    # element = (service, package, vuln): upgrading P in one service must not mark Q's rows fixed
    steps = greedy_set_cover(
        {(r["service_id"], r["package_id"], r["vuln_id"]) for r in open_rows},
        {pid: {(r["service_id"], pid, r["vuln_id"]) for r in rs} for pid, rs in by_pkg.items()},
    )
    plan = []
    for s in steps:
        fixed = set(s["vulns_fixed"])
        rs = [
            r
            for r in by_pkg[s["package"]]
            if (r["service_id"], r["package_id"], r["vuln_id"]) in fixed
        ]
        plan.append(
            {
                "step": s["step"],
                "package": rs[0]["package_name"],
                "package_id": s["package"],
                "ecosystem": rs[0]["ecosystem"],
                "from_version": ", ".join(sorted({r["version_string"] for r in rs}, key=_vkey)),
                "to_version": max((r["fixed_in"] for r in rs), key=_vkey),
                "vulns_fixed": sorted({r["osv_id"] for r in rs}),
                "vuln_ids": sorted({r["vuln_id"] for r in rs}),
                "services_affected": sorted({r["service_name"] for r in rs}),
                "new_coverage": s["new_coverage"],
                "cumulative_coverage_pct": s["cumulative_coverage_pct"],
            }
        )
    return plan


@router.get("/plan/{team_id}", response_model=Envelope[list[PlanStep]])
async def get_plan(team_id: int, session: DbSession) -> dict[str, Any]:
    return ok(await build_plan(session, team_id))


@router.post("/plan", response_model=Envelope[PlanOut], status_code=201)
async def create_plan(body: PlanCreate, session: DbSession) -> dict[str, Any]:
    plan = await build_plan(session, body.team_id)
    row = RemediationPlan(
        team_id=body.team_id,
        title=body.title or f"Remediation plan ({len(plan)} steps)",
        coverage_pct=plan[-1]["cumulative_coverage_pct"] if plan else 0,
    )
    session.add(row)
    await session.flush()
    actions = [
        RemediationAction(
            plan_id=row.plan_id,
            action_seq=s["step"],
            description=f"Upgrade {s['package']} {s['from_version']} -> {s['to_version']} "
            f"(fixes {len(s['vuln_ids'])} vulnerabilities)",
        )
        for s in plan
    ]
    session.add_all(actions)
    await session.flush()
    for s in plan:
        await session.execute(
            text("INSERT INTO resolves (plan_id, action_seq, vuln_id) VALUES (:p, :q, :v)"),
            [{"p": row.plan_id, "q": s["step"], "v": v} for v in s["vuln_ids"]],
        )
    await session.commit()
    return ok(
        {
            "plan_id": row.plan_id,
            "team_id": row.team_id,
            "title": row.title,
            "status": "DRAFT",
            "coverage_pct": row.coverage_pct,
            "actions": [
                {"action_seq": a.action_seq, "description": a.description, "status": "PENDING"}
                for a in actions
            ],
        }
    )


@router.patch("/plan/{plan_id}/actions/{seq}", response_model=Envelope[ActionOut])
async def patch_action(
    plan_id: int, seq: int, body: ActionPatch, session: DbSession
) -> dict[str, Any]:
    updated = (
        await session.execute(
            update(RemediationAction)
            .where(RemediationAction.plan_id == plan_id, RemediationAction.action_seq == seq)
            .values(status=body.status)
            .returning(RemediationAction.description, RemediationAction.status)
        )
    ).first()
    if updated is None:
        raise HTTPException(404, "Action not found")
    await session.commit()
    return ok({"action_seq": seq, "description": updated.description, "status": updated.status})
