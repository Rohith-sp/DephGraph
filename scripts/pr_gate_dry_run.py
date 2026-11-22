"""Run the PR gate for a service and a list of dependencies, without GitHub.

    python scripts/pr_gate_dry_run.py --service 1 --dep pypi:httpx:0.27.0 --dep npm:lodash:4.17.15
    python scripts/pr_gate_dry_run.py --service 1 --dep pypi:httpx:0.27.0 --json
    python scripts/pr_gate_dry_run.py --service 1 --dep pypi:httpx:0.27.0 --pr 42 --save

Uses the real database from the environment (DATABASE_URL, MONGO_URI, ... as apps/api settings,
or apps/api/.env.local). Stages 1-2 always run; Stage 3 (Groq) runs only when GROQ_API_KEY is set.
Read-only by default: pr_review / pr_risk_verdict rows (and the Mongo review_responses log) are
written only with --save.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import pr_gate_engine  # noqa: E402
from app.services.queries import lock_service, require_service, rows  # noqa: E402

DRY_RUN_AUTHOR = "pr-gate-dry-run"


def parse_dep(spec: str) -> tuple[str, str, str]:
    """'ecosystem:name:version' -> tuple. The version is everything after the second colon."""
    parts = spec.split(":", 2)
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise argparse.ArgumentTypeError(f"expected ecosystem:name:version, got {spec!r}")
    eco, name, ver = (p.strip() for p in parts)
    return eco.lower(), name, ver


async def dry_run(
    session: AsyncSession,
    service_id: int,
    deps: list[tuple[str, str, str]],
    pr_number: int = 0,
    save: bool = False,
) -> dict[str, Any]:
    """Stages 1-2 (one read transaction), Stage 3 if a Groq key is set, optional save."""
    async with session.begin():
        svc = await rows(
            session, "SELECT service_id, name FROM service WHERE service_id = :id", id=service_id
        )
        if not svc:
            raise LookupError(f"service {service_id} not found")
        results = await pr_gate_engine.evaluate_all(session, deps, service_id)
    verdict, stage = await pr_gate_engine.refine(
        results, service_id, pr_number, store_responses=save
    )
    if save:
        from app.routers.pr_gate import _store  # router helper: same rows as the webhook

        pr = {"html_url": "", "user": {"login": DRY_RUN_AUTHOR}}
        async with session.begin():
            await lock_service(session, service_id)
            await require_service(session, service_id)
            await _store(session, service_id, pr_number, pr, results)
    return {
        "service_id": service_id,
        "service": svc[0]["name"],
        "pr_number": pr_number,
        "verdict": verdict,
        "stage_reached": stage,
        "groq": bool(settings.groq_api_key),
        "saved": save,
        "deps": [
            {
                "ecosystem": eco,
                "name": d.name,
                "version": d.version,
                "verdict": d.verdict,
                "stage_reached": d.stage_reached,
                "reasoning": d.reasoning,
            }
            for (eco, _, _), d in zip(deps, results)
        ],
    }


def format_table(report: dict[str, Any]) -> str:
    head = ("dependency", "verdict", "stage", "reasoning")
    body = [
        (f"{d['ecosystem']}:{d['name']}=={d['version']}", d["verdict"], str(d["stage_reached"]),
         d["reasoning"])
        for d in report["deps"]
    ]
    widths = [max(len(r[i]) for r in [head, *body]) for i in range(3)]

    def line(r: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(r[:3], widths)) + "  " + r[3]

    out = [
        f"service {report['service_id']} ({report['service']}), "
        f"stage 3 {'on' if report['groq'] else 'off (no GROQ_API_KEY)'}",
        line(head),
        line(tuple("-" * w for w in widths) + ("-" * 9,)),
        *(line(r) for r in body),
        f"overall: {report['verdict']} (stage {report['stage_reached']})"
        + (f", saved as PR #{report['pr_number']}" if report["saved"] else ", not saved"),
    ]
    return "\n".join(out)


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--service", type=int, required=True, help="service_id")
    ap.add_argument(
        "--dep", type=parse_dep, action="append", required=True,
        help="ecosystem:name:version (repeatable)",
    )
    ap.add_argument("--pr", type=int, default=0, help="PR number to report/save under (0)")
    ap.add_argument("--save", action="store_true", help="write pr_review/pr_risk_verdict rows")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = ap.parse_args(argv)

    from app.database import SessionLocal, close_all

    try:
        async with SessionLocal() as session:
            report = await dry_run(session, args.service, args.dep, args.pr, args.save)
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        await pr_gate_engine.close_groq()
        await close_all()
    print(json.dumps(report, indent=2, default=str) if args.json else format_table(report))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
