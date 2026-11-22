"""Vulnerability alerts as GitHub Issues: one issue per (service repo, vuln) that reaches a
production service at or above its team's threshold; closed again once it no longer reaches it.

GitHub's own notifications (watchers' email / mobile) are the delivery. `run` is called after
GitHub imports, osv-sync and the retry sweep (never inside a user request); it never raises.
Work is bounded (MAX_WRITES real GitHub writes per run) and sequential. A run holds a Redis lock,
and an alert_issue row in state 'opening' is written before the issue is created, so overlapping
runs and crashes cannot open a second issue. A repo the App cannot write to is parked in
alert_skip for a while so it does not starve the others.
"""

import asyncio
import re
from collections.abc import Collection
from typing import Any
from urllib.parse import quote

import httpx
import structlog
from redis.exceptions import RedisError
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal, get_redis
from app.services import github, http
from app.services.queries import LATEST, REPO_URL_SQL, rows

log = structlog.get_logger()
MAX_WRITES = 20  # GitHub writes (issue / comment / state change) per run
CANDIDATES = 100  # rows read per query; skipped repos do not consume the write budget
PAUSE = 1.0  # seconds between GitHub writes: its secondary rate limit asks for >= 1 s
LOCK_KEY = "dephgraph:lock:alerts"
LOCK_TTL = 300
STALE_OPENING_HOURS = 1  # an 'opening' placeholder this old is presumed lost: drop it and retry
SKIP_NOT_INSTALLED_HOURS = 6
SKIP_FORBIDDEN_HOURS = 1
PERMISSIONS = {"issues": "write"}
LABEL = {"name": "dephgraph", "color": "b60205", "description": "Raised by DephGraph"}
_SEVERITIES = "ARRAY['LOW','MEDIUM','HIGH','CRITICAL']"
_GITHUB_REPO = f"{REPO_URL_SQL} LIKE 'https://github.com/%/%'"
_SKIPPED = "EXISTS (SELECT 1 FROM alert_skip k WHERE k.repo = {repo} AND k.until > now())"
TO_OPEN = f"""
    WITH {LATEST}
    SELECT s.service_id, s.name AS service_name, s.repo_url, s.github_installation_id AS installation_id,
           v.vuln_id, v.osv_id, v.severity, ai.issue_number, min(p.name) AS package,
           array_agg(DISTINCT p.name || '@' || a.version_string || CASE WHEN r.is_direct
             THEN ' (direct)' ELSE ' (transitive, depth ' || r.depth || ')' END) AS affected,
           array_agg(DISTINCT p.name || ' >= ' || a.fixed_in)
             FILTER (WHERE a.fixed_in IS NOT NULL) AS fixes
    FROM latest l
    JOIN service s ON s.service_id = l.service_id
    JOIN team t ON t.team_id = s.team_id AND t.alerts_enabled AND t.team_id = ANY(:teams)
    JOIN resolves_to r ON (r.service_id, r.deploy_seq) = (l.service_id, l.deploy_seq)
    JOIN affects a ON (a.package_id, a.version_string) = (r.package_id, r.version_string)
    JOIN vulnerability v ON v.vuln_id = a.vuln_id
    JOIN package p ON p.package_id = a.package_id
    LEFT JOIN alert_issue ai ON (ai.service_id, ai.vuln_id) = (s.service_id, v.vuln_id)
    WHERE {_GITHUB_REPO}
      AND NOT {_SKIPPED.format(repo=f"substr({REPO_URL_SQL}, 20)")}
      AND array_position({_SEVERITIES}, v.severity) >= array_position({_SEVERITIES}, t.alert_min_severity)
      AND ai.state IS DISTINCT FROM 'open' AND ai.state IS DISTINCT FROM 'opening'
      AND NOT EXISTS (
        SELECT 1 FROM waived_for w
        WHERE w.vuln_id = v.vuln_id AND w.service_id = s.service_id
          AND (w.expires_on IS NULL OR w.expires_on >= CURRENT_DATE))
    GROUP BY s.service_id, s.name, s.repo_url, s.github_installation_id, v.vuln_id, v.osv_id,
             v.severity, ai.issue_number
    ORDER BY array_position({_SEVERITIES}, v.severity) DESC, s.service_id, v.vuln_id
    LIMIT :n"""
# Closing ignores alerts_enabled (switching alerts off must not strand open issues) and also covers
# issues whose service was deleted (service_id NULL).
TO_CLOSE = f"""
    WITH {LATEST}
    SELECT ai.alert_id, ai.repo, ai.installation_id, ai.issue_number, v.osv_id,
           s.name AS service_name
    FROM alert_issue ai
    JOIN vulnerability v ON v.vuln_id = ai.vuln_id
    LEFT JOIN service s ON s.service_id = ai.service_id
    WHERE ai.state = 'open' AND ai.issue_number IS NOT NULL
      AND (s.service_id IS NULL OR s.team_id = ANY(:teams))
      AND NOT {_SKIPPED.format(repo="ai.repo")}
      AND (s.service_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM latest l
        JOIN resolves_to r ON (r.service_id, r.deploy_seq) = (l.service_id, l.deploy_seq)
        JOIN affects a ON (a.package_id, a.version_string) = (r.package_id, r.version_string)
        WHERE l.service_id = ai.service_id AND a.vuln_id = ai.vuln_id
          AND NOT EXISTS (
            SELECT 1 FROM waived_for w
            WHERE w.vuln_id = a.vuln_id AND w.service_id = l.service_id
              AND (w.expires_on IS NULL OR w.expires_on >= CURRENT_DATE))))
    ORDER BY ai.alert_id
    LIMIT :n"""


def _name(value: str) -> str:
    """Service names are user text: keep letters, digits, space, dot, dash, underscore."""
    return re.sub(r"[^\w .-]", "", value)[:80]


def _code(value: str) -> str:
    return value.replace("`", "'")


def _plain(value: str) -> str:
    """Text outside code spans: no @mentions (zero-width joiner after @), no backticks."""
    return _code(value).replace("@", "@\u200d")


def _issue_body(row: dict[str, Any]) -> str:
    osv = _name(row["osv_id"])
    fixed = (
        ", ".join(f"`{_code(f)}`" for f in row["fixes"] or []) or "no fixed version published yet"
    )
    link = f"{settings.web_url.rstrip('/')}/vulnerabilities?q={quote(osv)}"
    return (
        f"**{osv}** ({row['severity']}) reaches the production deployment of "
        f"`{_name(row['service_name'])}`.\n\nAffected:\n"
        + "".join(f"- `{_code(a)}`\n" for a in row["affected"])
        + f"\nFixed in: {fixed}\n\n[Open in DephGraph]({link})"
    )


def _rate_limited(e: httpx.HTTPError) -> bool:
    """429, or a 403 carrying GitHub's rate-limit signals (any other 403 is a permission problem)."""
    r = getattr(e, "response", None)
    return r is not None and (
        r.status_code == 429
        or (
            r.status_code == 403
            and ("retry-after" in r.headers or r.headers.get("x-ratelimit-remaining") == "0")
        )
    )


async def run(team_ids: Collection[int] | None = None) -> None:
    """Open/close issues for `team_ids` (every team when None). Never raises: alerting must not
    fail the import / cron run that triggered it. A no-op while another run holds the lock."""
    if not github.configured():
        return
    locked = False
    try:
        locked = bool(await get_redis().set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL))
        if not locked:
            log.info("alerts_skipped", reason="another run is active")
            return
    except (RedisError, OSError) as e:  # the 'opening' placeholder still prevents duplicates
        log.warning("alerts_lock_unavailable", error=str(e))
    try:
        await _run(team_ids)
    except Exception:  # noqa: BLE001 -- a side effect of import / osv-sync, retried by the next run
        log.exception("alerts_failed")
    finally:
        if locked:
            try:
                await get_redis().delete(LOCK_KEY)
            except (RedisError, OSError):
                pass  # expires by itself


async def _run(team_ids: Collection[int] | None) -> None:
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text(
                "DELETE FROM alert_issue WHERE state = 'opening' "
                "AND opened_at < now() - make_interval(hours => :h)"
            ),
            {"h": STALE_OPENING_HOURS},
        )
        teams = (
            sorted(team_ids)
            if team_ids is not None
            else [r["team_id"] for r in await rows(s, "SELECT team_id FROM team")]
        )
        closing = await rows(s, TO_CLOSE, teams=teams, n=CANDIDATES)
        opening = await rows(s, TO_OPEN, teams=teams, n=CANDIDATES)
    work = [(_close, r) for r in closing] + [(_open, r) for r in opening]
    tokens: dict[str, str] = {}
    parked: set[str] = set()
    labelled: set[str] = set()
    writes = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for action, row in work:
            if writes >= MAX_WRITES:
                break
            repo = row.get("repo") or github.full_name(row["repo_url"])
            if repo is None or repo in parked:
                continue
            if repo not in tokens:
                try:
                    token = await github.repo_token(
                        client, repo, PERMISSIONS, row["installation_id"]
                    )
                except httpx.HTTPError as e:
                    log.warning("alert_token_failed", repo=repo, error=str(e))
                    token = None
                    await _park(repo, SKIP_FORBIDDEN_HOURS)
                else:
                    if token is None:
                        log.info("alert_skipped_no_installation", repo=repo)
                        await _park(repo, SKIP_NOT_INSTALLED_HOURS)
                if token is None:
                    parked.add(repo)
                    continue
                tokens[repo] = token
            if writes:
                await asyncio.sleep(PAUSE)
            writes += 1
            try:
                await action(client, repo, tokens[repo], row, labelled)
            except httpx.HTTPError as e:
                log.warning("alert_failed", repo=repo, vuln=row["osv_id"], error=str(e))
                if _rate_limited(e):
                    break  # the rest waits for the next run
                if getattr(e, "response", None) is not None and e.response.status_code == 403:
                    await _park(repo, SKIP_FORBIDDEN_HOURS)
                    parked.add(repo)


async def _park(repo: str, hours: int) -> None:
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO alert_skip (repo, until) VALUES (:r, now() + make_interval(hours => :h)) "
                "ON CONFLICT (repo) DO UPDATE SET until = excluded.until"
            ),
            {"r": repo, "h": hours},
        )


def _headers(token: str) -> dict[str, str]:
    return {**github.HEADERS, "Authorization": f"Bearer {token}"}


async def _call(
    client: httpx.AsyncClient, method: str, url: str, token: str, **kw: Any
) -> httpx.Response:
    """GitHub write, sent once: a retried POST after a 5xx could create the resource twice."""
    r = await http.request(
        client, method, f"{github.API}{url}", headers=_headers(token), attempts=1, **kw
    )
    r.raise_for_status()
    return r


def _ambiguous(e: httpx.HTTPError) -> bool:
    """The request may have taken effect (no response, or a 5xx)."""
    r = getattr(e, "response", None)
    return r is None or r.status_code >= 500


async def _set_row(alert_id: int, **fields: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text(f"UPDATE alert_issue SET {sets} WHERE alert_id = :id"), {"id": alert_id, **fields}
        )


async def _create_issue(
    client: httpx.AsyncClient, repo: str, token: str, row: dict[str, Any], labelled: set[str]
) -> int:
    if repo not in labelled:  # 422 = the label already exists
        r = await http.request(
            client,
            "POST",
            f"{github.API}/repos/{repo}/labels",
            headers=_headers(token),
            attempts=1,
            json=LABEL,
        )
        if r.status_code not in (201, 422):
            r.raise_for_status()
        labelled.add(repo)
    r = await _call(
        client,
        "POST",
        f"/repos/{repo}/issues",
        token,
        json={
            "title": f"[DephGraph] {_plain(row['osv_id'])} ({row['severity']}) in {_plain(row['package'])}",
            "body": _issue_body(row),
            "labels": [LABEL["name"]],
        },
    )
    return int(r.json()["number"])


async def _open(
    client: httpx.AsyncClient, repo: str, token: str, row: dict[str, Any], labelled: set[str]
) -> None:
    prior = row["issue_number"]
    async with SessionLocal() as s, s.begin():
        claimed = await rows(
            s,
            "INSERT INTO alert_issue (service_id, vuln_id, repo, installation_id, state) "
            "VALUES (:s, :v, :r, :i, 'opening') ON CONFLICT (service_id, vuln_id) DO UPDATE "
            "SET state = 'opening', opened_at = now(), closed_at = NULL, repo = excluded.repo, "
            "installation_id = excluded.installation_id WHERE alert_issue.state = 'closed' "
            "RETURNING alert_id",
            s=row["service_id"],
            v=row["vuln_id"],
            r=repo,
            i=row["installation_id"],
        )
    if not claimed:  # another run holds it
        return
    alert_id = claimed[0]["alert_id"]
    try:
        number = None
        if prior is not None:  # closed earlier, exposed again: reopen the same issue
            try:
                await _call(
                    client,
                    "POST",
                    f"/repos/{repo}/issues/{prior}/comments",
                    token,
                    json={"body": "DephGraph: this vulnerability reaches production again."},
                )
                await _call(
                    client, "PATCH", f"/repos/{repo}/issues/{prior}", token, json={"state": "open"}
                )
                number = prior
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (404, 410):  # issue deleted by hand: new one
                    raise
        if number is None:
            number = await _create_issue(client, repo, token, row, labelled)
    except httpx.HTTPError as e:
        if prior is not None:  # reopening is repeatable: back to closed, tried again next run
            await _set_row(alert_id, state="closed", closed_at=None)
        elif not _ambiguous(e):  # nothing was created
            async with SessionLocal() as s, s.begin():
                await s.execute(
                    text("DELETE FROM alert_issue WHERE alert_id = :i"), {"i": alert_id}
                )
        # else: possibly created; the placeholder stays so no second issue is opened (it expires)
        raise
    await _set_row(alert_id, issue_number=number, state="open")


async def _close(
    client: httpx.AsyncClient, repo: str, token: str, row: dict[str, Any], _: set[str]
) -> None:
    number = row["issue_number"]
    try:
        await _call(
            client,
            "POST",
            f"/repos/{repo}/issues/{number}/comments",
            token,
            json={
                "body": f"DephGraph: {_name(row['osv_id'])} no longer reaches the production "
                f"deployment of `{_name(row['service_name'] or 'a deleted service')}` "
                "(upgraded, removed, waived or the service was deleted). Closing."
            },
        )
        await _call(
            client,
            "PATCH",
            f"/repos/{repo}/issues/{number}",
            token,
            json={"state": "closed", "state_reason": "completed"},
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in (404, 410):  # issue deleted by hand: nothing to close
            raise
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text("UPDATE alert_issue SET state = 'closed', closed_at = now() WHERE alert_id = :i"),
            {"i": row["alert_id"]},
        )
