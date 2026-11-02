"""PR gate: 3-stage dependency verdicts (PRD §3.3, §8). Stage 2 rules from §8 are authoritative.

Decisions: a vuln with NULL cvss_score is ADVISORY (unknown severity, never silently PASS);
overall PR verdict = worst dependency verdict; Stage 1 only fast-exits when the version has no
`affects` row AND is neither copyleft nor abandoned >= 40 (§8: 40-79 is ADVISORY, >= 80 BLOCK). NULL abandonment (no version row;
the view COALESCEs a missing publish date to 50) counts as 0.
"""

import asyncio
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog
from groq import APIError, AsyncGroq, RateLimitError
from pymongo.errors import PyMongoError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_mongo
from app.services import http
from app.services.queries import rows

log = structlog.get_logger()

RANK = {"PASS": 0, "ADVISORY": 1, "BLOCK": 2}
MAX_PROMPT_CHARS = 1200  # ~300 tokens at chars/4
LLM_ATTEMPTS = 4
GROQ_TIMEOUT = 8  # seconds per request
GROQ_CONCURRENCY = 3
STAGE3_BUDGET = 15  # seconds for all Stage 3 calls together; keeps the webhook well under 30s


@dataclass
class DepVerdict:
    name: str
    version: str
    verdict: str = "PASS"
    stage_reached: int = 1
    reasoning: str = ""
    context_tokens: int | None = None
    facts: dict[str, Any] = field(default_factory=dict)


def stage2(
    cvss: list[Decimal | None],
    fixed: list[bool],
    copyleft: bool,
    abandonment: int | None,
) -> tuple[str, str]:
    """Pure rule table. cvss/fixed are parallel per-vuln lists."""
    block, advisory = [], []
    if copyleft:
        block.append("copyleft license")
    if abandonment is not None and abandonment >= 80:
        block.append(f"abandonment score {abandonment} >= 80")
    elif abandonment is not None and abandonment >= 40:
        advisory.append(f"abandonment score {abandonment} in 40-79")
    for score, has_fix in zip(cvss, fixed):
        if score is None:
            advisory.append("vulnerability with unknown CVSS")
        elif score >= 9:
            block.append(f"CVSS {score} >= 9.0")
        elif score >= 6:
            advisory.append(f"CVSS {score} in 6.0-8.9")
        if not has_fix:
            advisory.append("vulnerability with no fixed version")
    if block:
        return "BLOCK", "; ".join(dict.fromkeys(block))
    if advisory:
        return "ADVISORY", "; ".join(dict.fromkeys(advisory))
    return "PASS", ("no vulnerabilities" if not cvss else "all CVSS < 6.0 with a fix available")


async def _signals(session: AsyncSession, eco: str, name: str, ver: str) -> dict[str, Any]:
    found = await rows(
        session,
        """
        SELECT COALESCE(l.is_copyleft, FALSE) AS copyleft, ab.abandonment_score AS abandonment
        FROM package p
        JOIN package_version pv ON pv.package_id = p.package_id AND pv.version_string = :ver
        LEFT JOIN license l ON l.license_id = pv.license_id
        LEFT JOIN v_abandonment ab ON (ab.ecosystem, ab.name, ab.version_string)
                                    = (p.ecosystem, p.name, pv.version_string)
        WHERE p.ecosystem = :eco AND p.name = :name
        """,
        eco=eco,
        name=name,
        ver=ver,
    )
    return found[0] if found else {"copyleft": False, "abandonment": None}


async def evaluate(session: AsyncSession, eco: str, name: str, ver: str) -> DepVerdict:
    dep = DepVerdict(name, ver)
    has_affects = await rows(
        session,
        "SELECT 1 FROM affects a JOIN package p ON p.package_id = a.package_id "
        "WHERE p.ecosystem = :eco AND p.name = :name AND a.version_string = :ver LIMIT 1",
        eco=eco,
        name=name,
        ver=ver,
    )
    sig = await _signals(session, eco, name, ver)
    if not has_affects and not sig["copyleft"] and (sig["abandonment"] or 0) < 40:
        dep.reasoning = "no known vulnerabilities (stage 1)"
        return dep
    vulns = await rows(
        session,
        "SELECT v.osv_id, v.cvss_score, a.fixed_in FROM affects a "
        "JOIN package p ON p.package_id = a.package_id "
        "JOIN vulnerability v ON v.vuln_id = a.vuln_id "
        "WHERE p.ecosystem = :eco AND p.name = :name AND a.version_string = :ver ORDER BY v.osv_id",
        eco=eco,
        name=name,
        ver=ver,
    )
    dep.verdict, dep.reasoning = stage2(
        [v["cvss_score"] for v in vulns],
        [v["fixed_in"] is not None for v in vulns],
        sig["copyleft"],
        sig["abandonment"],
    )
    dep.stage_reached = 2
    dep.facts = {**sig, "vulns": vulns}
    return dep


def build_prompt(dep: DepVerdict) -> str:
    """<= MAX_PROMPT_CHARS; variable parts are cut first so the JSON instruction always survives."""
    head = (
        f"Dependency {dep.name[:100]}=={dep.version[:50]} added in a PR. Rule-based verdict: "
        f"{dep.verdict} ({dep.reasoning[:300]}). copyleft={dep.facts.get('copyleft')} "
        f"abandonment={dep.facts.get('abandonment')}. Vulns: "
    )
    tail = '.\nReply JSON only: {"verdict":"ADVISORY"|"BLOCK","reasoning":"<=2 sentences"}'
    vulns = ", ".join(
        f"{v['osv_id']} cvss={v['cvss_score']} fixed_in={v['fixed_in']}"
        for v in dep.facts.get("vulns", [])
    )
    room = MAX_PROMPT_CHARS - len(head) - len(tail)
    if len(vulns) > room:
        vulns = vulns[: room - 1] + "…"
    return head + (vulns or "none") + tail


_groq: AsyncGroq | None = None


def _client() -> AsyncGroq:
    global _groq
    if _groq is None:
        _groq = AsyncGroq(api_key=settings.groq_api_key, max_retries=0, timeout=GROQ_TIMEOUT)
    return _groq


async def close_groq() -> None:
    global _groq
    if _groq is not None:
        await _groq.close()
        _groq = None


async def _ask_groq(prompt: str) -> str:
    client = _client()
    for attempt in range(LLM_ATTEMPTS):
        try:
            resp = await client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=200,
            )
            return resp.choices[0].message.content or ""
        except RateLimitError:
            if attempt == LLM_ATTEMPTS - 1:
                raise
            await asyncio.sleep(http.BACKOFF * 2**attempt * (1 + random.random()))
    raise AssertionError("unreachable")


async def stage3(dep: DepVerdict, service_id: int, pr_number: int) -> None:
    """Refine an ADVISORY with Groq; any failure keeps the Stage 2 verdict (stage_reached=2)."""
    if not settings.groq_api_key:
        return
    prompt = build_prompt(dep)
    try:
        raw = await _ask_groq(prompt)
        out = json.loads(raw)
        verdict = out["verdict"]
        if verdict not in ("ADVISORY", "BLOCK"):
            raise ValueError(f"bad verdict {verdict!r}")
    except (APIError, ValueError, KeyError, TypeError) as e:
        log.warning("stage3_failed", dep=dep.name, error=str(e))
        return
    dep.verdict, dep.stage_reached = verdict, 3
    dep.reasoning = str(out.get("reasoning", ""))[:1000] or dep.reasoning
    dep.context_tokens = len(prompt) // 4
    try:
        await get_mongo()["review_responses"].insert_one(
            {
                "service_id": service_id,
                "pr_number": pr_number,
                "dep_name": dep.name,
                "model": settings.groq_model,
                "prompt": prompt,
                "response": raw,
                "responded_at": datetime.now(timezone.utc),
            }
        )
    except PyMongoError as e:
        log.warning("review_response_not_stored", error=str(e))


async def evaluate_all(session: AsyncSession, deps: list[tuple[str, str, str]]) -> list[DepVerdict]:
    """Stages 1-2 (DB reads only). deps = (ecosystem, name, version)."""
    return [await evaluate(session, *d) for d in deps]


async def refine(out: list[DepVerdict], service_id: int, pr_number: int) -> tuple[str, int]:
    """Stage 3 for ADVISORY deps, concurrently, no DB transaction open. Returns (overall, stage)."""
    sem = asyncio.Semaphore(GROQ_CONCURRENCY)

    async def one(d: DepVerdict) -> None:
        async with sem:
            await stage3(d, service_id, pr_number)

    todo = [one(d) for d in out if d.verdict == "ADVISORY"]
    try:
        await asyncio.wait_for(asyncio.gather(*todo), STAGE3_BUDGET)
    except TimeoutError:  # unfinished deps keep their Stage 2 verdict
        log.warning("stage3_budget_exceeded", budget=STAGE3_BUDGET)
    overall = max((d.verdict for d in out), key=RANK.__getitem__, default="PASS")
    return overall, max((d.stage_reached for d in out), default=1)
