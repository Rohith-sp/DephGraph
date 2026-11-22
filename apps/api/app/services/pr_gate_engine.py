"""PR gate: 3-stage dependency verdicts (PRD §3.3, §8). Stage 2 rules from §8 are authoritative.

Decisions: a vuln with NULL cvss_score is ADVISORY (unknown severity, never silently PASS);
overall PR verdict = worst dependency verdict; Stage 1 only fast-exits when the version has no
`affects` row AND is neither copyleft nor abandoned >= 40 (§8: 40-79 is ADVISORY, >= 80 BLOCK). NULL abandonment (no version row;
the view COALESCEs a missing publish date to 50) counts as 0.

Semantic redundancy (third signal): the added package shares a `semantic_cluster` with a DIFFERENT
package of the same ecosystem that the service's latest deployment already resolves -> ADVISORY. It is checked before the
Stage 1 fast exit, so a clean-but-redundant dependency still reaches Stage 2. The cluster comes
from the added version's row; if that version is not in the DB (or is unclustered), the clusters
of the package's other versions are used. Unknown package or no cluster -> no signal. Only packages NEW to the service count: a version
bump of a package its latest deployment already resolves adds no redundancy.
"""

import asyncio
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

import structlog
from groq import APIError, AsyncGroq, RateLimitError
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_mongo, get_redis
from app.services import http
from app.services.queries import rows

log = structlog.get_logger()

RANK = {"PASS": 0, "ADVISORY": 1, "BLOCK": 2}
MAX_PROMPT_CHARS = 1200  # ~300 tokens at chars/4
MAX_REDUNDANCY_CHARS = 200  # redundancy facts' share of the prompt; vulns get the rest
LLM_ATTEMPTS = 4
GROQ_TIMEOUT = 8  # seconds per request
GROQ_CONCURRENCY = 3
STAGE3_BUDGET = 15  # seconds for all Stage 3 calls together (runs in a background task)
PROMPT_CACHE_TTL = 604800  # PRD: Redis `prompts`, 7 days


@dataclass
class DepVerdict:
    name: str
    version: str
    verdict: str = "PASS"
    stage_reached: int = 1
    reasoning: str = ""
    context_tokens: int | None = None
    facts: dict[str, Any] = field(default_factory=dict)


REDUNDANT_NAMES_SHOWN = 3  # per cluster in the reasoning; the rest become "+N more"


def redundancy_reasons(redundancy: list[dict[str, Any]]) -> list[str]:
    """[{package, cluster_label}, ...] -> one "same job as ..." reason per cluster (pure)."""
    by_cluster: dict[str, list[str]] = {}
    for r in redundancy:
        names = by_cluster.setdefault(str(r["cluster_label"]), [])
        if str(r["package"]) not in names:
            names.append(str(r["package"]))
    out = []
    for label, names in by_cluster.items():
        shown = ", ".join(names[:REDUNDANT_NAMES_SHOWN])
        more = len(names) - REDUNDANT_NAMES_SHOWN
        extra = f" +{more} more" if more > 0 else ""
        out.append(f"same job as {shown}{extra} (cluster '{label}')")
    return out


def stage2(
    cvss: list[Decimal | None],
    fixed: list[bool],
    copyleft: bool,
    abandonment: int | None,
    redundancy: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Pure rule table. cvss/fixed are parallel per-vuln lists.

    redundancy = packages of the same semantic cluster already in the service's latest deployment
    ({package, cluster_label} dicts); any entry makes the dependency at least ADVISORY.
    """
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
    advisory.extend(redundancy_reasons(redundancy or []))
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


async def _redundancy(
    session: AsyncSession, service_id: int | None, eco: str, name: str, ver: str
) -> list[dict[str, Any]]:
    """Other packages in the service's LATEST deployment that share a cluster with eco/name.

    Same ecosystem only: an npm and a pypi package in one cluster do not do the same job inside
    one runtime (clusters are built from descriptions and can mix ecosystems).
    """
    if service_id is None:
        return []
    return await rows(
        session,
        """
        WITH dep_clusters AS (
          SELECT DISTINCT pv.cluster_id
          FROM package p
          JOIN package_version pv ON pv.package_id = p.package_id
          WHERE p.ecosystem = :eco AND p.name = :name AND pv.cluster_id IS NOT NULL
            AND (pv.version_string = :ver OR NOT EXISTS (
                  SELECT 1 FROM package_version x
                  WHERE x.package_id = p.package_id AND x.version_string = :ver
                    AND x.cluster_id IS NOT NULL))
        ),
        latest AS (SELECT MAX(deploy_seq) AS deploy_seq FROM deployment WHERE service_id = :sid)
        SELECT DISTINCT p2.name AS package, sc.cluster_id, sc.label AS cluster_label
        FROM resolves_to rt
        JOIN latest ld ON rt.deploy_seq = ld.deploy_seq
        JOIN package_version pv2 ON (pv2.package_id, pv2.version_string)
                                  = (rt.package_id, rt.version_string)
        JOIN dep_clusters dc ON dc.cluster_id = pv2.cluster_id
        JOIN semantic_cluster sc ON sc.cluster_id = pv2.cluster_id
        JOIN package p2 ON p2.package_id = rt.package_id
        WHERE rt.service_id = :sid AND p2.ecosystem = :eco AND p2.name <> :name
          AND NOT EXISTS (  -- already deployed: a bump, not a new redundancy
            SELECT 1 FROM resolves_to r0
            JOIN package p0 ON p0.package_id = r0.package_id
            WHERE r0.service_id = :sid AND r0.deploy_seq = ld.deploy_seq
              AND p0.ecosystem = :eco AND p0.name = :name)
        ORDER BY sc.cluster_id, p2.name
        """,
        sid=service_id,
        eco=eco,
        name=name,
        ver=ver,
    )


async def evaluate(
    session: AsyncSession, eco: str, name: str, ver: str, service_id: int | None = None
) -> DepVerdict:
    """Stages 1-2 for one dependency; service_id enables the redundancy signal."""
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
    redundancy = await _redundancy(session, service_id, eco, name, ver)
    if (
        not has_affects
        and not sig["copyleft"]
        and (sig["abandonment"] or 0) < 40
        and not redundancy
    ):
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
        redundancy,
    )
    dep.stage_reached = 2
    dep.facts = {**sig, "vulns": vulns, "redundancy": redundancy}
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
    redundant = "; ".join(redundancy_reasons(dep.facts.get("redundancy") or []))
    if len(redundant) > MAX_REDUNDANCY_CHARS:
        redundant = redundant[: MAX_REDUNDANCY_CHARS - 1] + "…"
    mid = f". Redundant with packages already deployed: {redundant or 'none'}"
    room = MAX_PROMPT_CHARS - len(head) - len(mid) - len(tail)
    if len(vulns) > room:
        vulns = vulns[: room - 1] + "…"
    return head + (vulns or "none") + mid + tail


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
                max_tokens=1024,  # gpt-oss spends tokens on reasoning before the JSON answer
            )
            return resp.choices[0].message.content or ""
        except RateLimitError:
            if attempt == LLM_ATTEMPTS - 1:
                raise
            await asyncio.sleep(http.BACKOFF * 2**attempt * (1 + random.random()))
    raise AssertionError("unreachable")


def _redundancy_only(dep: DepVerdict) -> bool:
    """Only the redundancy signal fired: advisory by nature, an LLM must not escalate it to BLOCK."""
    f = dep.facts
    return bool(
        f.get("redundancy")
        and not f.get("vulns")
        and not f.get("copyleft")
        and (f.get("abandonment") or 0) < 40
    )


async def _cached_reply(key: str) -> str | None:
    try:
        return await get_redis().get(key)
    except (RedisError, OSError) as e:
        log.warning("prompt_cache_read_failed", error=str(e))
        return None


async def stage3(
    dep: DepVerdict, service_id: int, pr_number: int, store_response: bool = True
) -> None:
    """Refine an ADVISORY with Groq; any failure keeps the Stage 2 verdict (stage_reached=2).

    Replies are cached by prompt hash for 7 days (redelivery/synchronize of unchanged deps).
    store_response=False skips the Mongo `review_responses` log (offline dry runs)."""
    if not settings.groq_api_key or _redundancy_only(dep):
        return
    prompt = build_prompt(dep)
    key = f"dephgraph:prompts:{sha256(prompt.encode()).hexdigest()}"
    raw = await _cached_reply(key)
    fresh = raw is None
    try:
        if raw is None:
            raw = await _ask_groq(prompt)
        out = json.loads(raw)
        verdict = str(out["verdict"]).strip().upper()
        if verdict not in ("ADVISORY", "BLOCK"):  # never PASS: the LLM cannot downgrade
            raise ValueError(f"bad verdict {verdict!r}")
    except (APIError, ValueError, KeyError, TypeError) as e:
        log.warning("stage3_failed", dep=dep.name, error=str(e))
        return
    llm = str(out.get("reasoning", "")).strip()
    dep.verdict, dep.stage_reached = verdict, 3
    dep.reasoning = f"{dep.reasoning[:300]} | LLM: {llm[:600]}" if llm else dep.reasoning
    dep.context_tokens = len(prompt) // 4
    if not fresh:
        return
    try:
        await get_redis().set(key, raw, nx=True, ex=PROMPT_CACHE_TTL)
    except (RedisError, OSError) as e:
        log.warning("prompt_cache_write_failed", error=str(e))
    if not store_response:
        return
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


async def evaluate_all(
    session: AsyncSession,
    deps: list[tuple[str, str, str]],
    service_id: int | None = None,
) -> list[DepVerdict]:
    """Stages 1-2 (DB reads only). deps = (ecosystem, name, version)."""
    return [await evaluate(session, *d, service_id=service_id) for d in deps]


async def refine(
    out: list[DepVerdict], service_id: int, pr_number: int, store_responses: bool = True
) -> tuple[str, int]:
    """Stage 3 for ADVISORY deps, concurrently, no DB transaction open. Returns (overall, stage)."""
    sem = asyncio.Semaphore(GROQ_CONCURRENCY)

    async def one(d: DepVerdict) -> None:
        async with sem:
            await stage3(d, service_id, pr_number, store_responses)

    todo = [one(d) for d in out if d.verdict == "ADVISORY"]
    try:
        await asyncio.wait_for(asyncio.gather(*todo), STAGE3_BUDGET)
    except TimeoutError:  # unfinished deps keep their Stage 2 verdict
        log.warning("stage3_budget_exceeded", budget=STAGE3_BUDGET)
    return overall(out)


def overall(out: list[DepVerdict]) -> tuple[str, int]:
    """(worst verdict, highest stage reached); PASS/1 when empty."""
    return (
        max((d.verdict for d in out), key=RANK.__getitem__, default="PASS"),
        max((d.stage_reached for d in out), default=1),
    )


def _worst_per_name(results: list[DepVerdict]) -> list[DepVerdict]:
    """The PK is (service, pr, dep_name) but deps differ by ecosystem/version: keep the worst."""
    worst: dict[str, DepVerdict] = {}
    for d in results:
        key = d.name[:255]
        if key not in worst or RANK[d.verdict] > RANK[worst[key].verdict]:
            worst[key] = d
    return list(worst.values())


def _event_time(pr: dict[str, Any]) -> datetime:
    """pull_request.updated_at (GitHub bumps it on every push); now() for payloads without it."""
    try:
        return datetime.fromisoformat(pr["updated_at"]).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return datetime.now(timezone.utc)


async def store_verdicts(
    session: AsyncSession,
    service_id: int,
    number: int,
    pr: dict[str, Any],
    results: list[DepVerdict],
) -> bool:
    """Replace the PR's review + verdict rows (caller holds the service lock in a transaction).

    Returns False, writing nothing, when the stored review comes from a newer event: overlapping
    deliveries finish in any order and an older head must not overwrite a newer one.
    """
    stored = await session.execute(
        text(
            "INSERT INTO pr_review (service_id, pr_number, pr_url, author, head_sha, evaluated_at) "
            "VALUES (:s, :n, :url, :author, :sha, :at) ON CONFLICT (service_id, pr_number) "
            "DO UPDATE SET pr_url = EXCLUDED.pr_url, author = EXCLUDED.author, "
            "head_sha = EXCLUDED.head_sha, evaluated_at = EXCLUDED.evaluated_at "
            "WHERE pr_review.evaluated_at IS NULL OR pr_review.evaluated_at <= EXCLUDED.evaluated_at "
            "RETURNING 1"
        ),
        {
            "s": service_id,
            "n": number,
            "url": str(pr.get("html_url", ""))[:500],
            "author": str((pr.get("user") or {}).get("login", ""))[:120] or None,
            "sha": str((pr.get("head") or {}).get("sha", ""))[:64] or None,
            "at": _event_time(pr),
        },
    )
    if stored.first() is None:
        return False
    await session.execute(
        text("DELETE FROM pr_risk_verdict WHERE service_id = :s AND pr_number = :n"),
        {"s": service_id, "n": number},
    )
    for d in _worst_per_name(results):
        await session.execute(
            text(
                "INSERT INTO pr_risk_verdict (service_id, pr_number, dep_name, risk_level, "
                "stage_reached, context_tokens, reasoning) "
                "VALUES (:s, :n, :dep, :v, :st, :ct, :why)"
            ),
            {
                "s": service_id,
                "n": number,
                "dep": d.name[:255],
                "v": d.verdict,
                "st": d.stage_reached,
                "ct": d.context_tokens,
                "why": d.reasoning,
            },
        )
    return True
