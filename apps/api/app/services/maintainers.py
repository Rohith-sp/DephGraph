"""Maintainers + last activity from public registries (no API keys) -> maintainer / maintained_by.

Feeds v_abandonment (migration 007): single-maintainer exposure + days since last activity.

Per ecosystem:
- npm:      GET registry.npmjs.org/<name>          -> `maintainers[].name`, `time.modified`
- pypi:     GET pypi.org/pypi/<name>/json          -> `info.maintainer` or else `info.author`
            (free-text names, comma separated; PyPI's JSON API does not list account owners),
            activity = newest upload time of the latest release (`urls[]`), else of any release
- cargo:    GET crates.io/api/v1/crates/<name>/owners -> `users[].login`,
            GET crates.io/api/v1/crates/<name>        -> `crate.updated_at`
            (crates.io requires a descriptive User-Agent; we send a generic one, no personal info)
- go/maven/nuget: skipped. The Go module proxy has no maintainer data and there is no free
            keyless owner API for the others, so these packages keep the v_abandonment fallback.

Activity is per package: a registry tells us when the package last changed, not when each person was
last active. A maintainer's last_active_at is therefore "newest activity of any package they maintain"
(GREATEST on upsert, never moves backwards).

Network calls happen outside any transaction (like routers/ingest.py); `upsert` runs inside the
caller's transaction.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx
import structlog
from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sql import MaintainedBy, Maintainer
from app.services.http import request

log = structlog.get_logger()

NPM_URL = "https://registry.npmjs.org"
PYPI_URL = "https://pypi.org/pypi"
CRATES_URL = "https://crates.io/api/v1/crates"
USER_AGENT = "dephgraph-course-project (maintainer abandonment scoring)"
CONCURRENCY = 8
SUPPORTED = ("npm", "pypi", "cargo")


@dataclass(frozen=True)
class Person:
    username: str
    profile_url: str | None
    role: str = "owner"


@dataclass(frozen=True)
class PackageMaintainers:
    platform: str  # maintainer.platform ('npm' | 'pypi' | 'crates.io')
    people: tuple[Person, ...]
    last_active_at: datetime | None


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)  # 3.11+: accepts "Z"
    except ValueError:
        return None


def _dedupe(people: list[Person]) -> tuple[Person, ...]:
    seen: dict[str, Person] = {}
    for p in people:
        name = p.username.strip()[:120]
        if name and name.lower() not in seen:
            seen[name.lower()] = Person(name, p.profile_url, p.role)
    return tuple(seen.values())


def parse_npm(doc: dict[str, Any]) -> PackageMaintainers:
    people = [
        Person(m["name"], f"https://www.npmjs.com/~{quote(m['name'])}")
        for m in doc.get("maintainers") or []
        if isinstance(m, dict) and isinstance(m.get("name"), str)
    ]
    return PackageMaintainers(
        "npm", _dedupe(people), _ts((doc.get("time") or {}).get("modified"))
    )


def parse_pypi(doc: dict[str, Any]) -> PackageMaintainers:
    info = doc.get("info") or {}
    raw, role = info.get("maintainer"), "maintainer"
    if not isinstance(raw, str) or not raw.strip():
        raw, role = info.get("author"), "author"
    names = raw.split(",") if isinstance(raw, str) else []
    people = [Person(n, None, role) for n in names]

    def newest(files: list[Any]) -> datetime | None:
        times = [
            t
            for f in files
            if isinstance(f, dict) and (t := _ts(f.get("upload_time_iso_8601")))
        ]
        return max(times, default=None)

    last = newest(doc.get("urls") or [])
    if last is None:
        last = newest(
            [f for files in (doc.get("releases") or {}).values() for f in files or []]
        )
    return PackageMaintainers("pypi", _dedupe(people), last)


def parse_crates(owners: dict[str, Any], crate: dict[str, Any]) -> PackageMaintainers:
    people = [
        Person(u["login"], u.get("url"), "team" if u.get("kind") == "team" else "owner")
        for u in owners.get("users") or []
        if isinstance(u, dict) and isinstance(u.get("login"), str)
    ]
    return PackageMaintainers(
        "crates.io", _dedupe(people), _ts((crate.get("crate") or {}).get("updated_at"))
    )


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    try:
        r = await request(client, "GET", url)
        if r.status_code != 200:
            log.warning("maintainers_miss", url=url, status=r.status_code)
            return None
        doc = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("maintainers_failed", url=url, error=str(e))
        return None
    return doc if isinstance(doc, dict) else None


async def fetch_one(
    client: httpx.AsyncClient, ecosystem: str, name: str
) -> PackageMaintainers | None:
    """None when the ecosystem is unsupported or the registry lookup failed."""
    if ecosystem == "npm":
        doc = await _get_json(client, f"{NPM_URL}/{quote(name, safe='@')}")
        return parse_npm(doc) if doc else None
    if ecosystem == "pypi":
        doc = await _get_json(client, f"{PYPI_URL}/{quote(name, safe='')}/json")
        return parse_pypi(doc) if doc else None
    if ecosystem == "cargo":
        crate_url = f"{CRATES_URL}/{quote(name, safe='')}"
        owners, crate = await asyncio.gather(
            _get_json(client, f"{crate_url}/owners"), _get_json(client, crate_url)
        )
        return parse_crates(owners, crate or {}) if owners else None
    return None  # go / maven / nuget: no keyless maintainer data


async def fetch_all(
    packages: list[tuple[int, str, str]],
) -> dict[int, PackageMaintainers]:
    """packages = [(package_id, ecosystem, name)]. Never raises; failed lookups are absent."""
    sem = asyncio.Semaphore(CONCURRENCY)

    async def bounded(
        pid: int, eco: str, name: str
    ) -> tuple[int, PackageMaintainers | None]:
        async with sem:
            return pid, await fetch_one(client, eco, name)

    wanted = [p for p in packages if p[1] in SUPPORTED]
    async with httpx.AsyncClient(
        timeout=15, headers={"User-Agent": USER_AGENT}
    ) as client:
        results = await asyncio.gather(*(bounded(*p) for p in wanted))
    return {pid: m for pid, m in results if m is not None}


async def upsert(session: AsyncSession, found: dict[int, PackageMaintainers]) -> int:
    """Upsert maintainer (+ last_active_at) and maintained_by; caller owns the transaction.

    For every package with at least one maintainer from the registry, maintained_by is replaced by
    exactly that set (registry is the source of truth). Returns the number of maintainers upserted.
    """
    found = {pid: m for pid, m in found.items() if m.people}
    if not found:
        return 0
    # one row per (platform, username): newest activity across all their packages
    people: dict[tuple[str, str], dict[str, Any]] = {}
    for m in found.values():
        for p in m.people:
            key = (m.platform, p.username)
            row = people.setdefault(
                key,
                {
                    "platform": m.platform,
                    "username": p.username,
                    "profile_url": p.profile_url,
                    "last_active_at": None,
                },
            )
            if m.last_active_at and (
                row["last_active_at"] is None
                or m.last_active_at > row["last_active_at"]
            ):
                row["last_active_at"] = m.last_active_at
    stmt = insert(Maintainer).values(
        [people[k] for k in sorted(people)]
    )  # sorted: lock order
    stmt = stmt.on_conflict_do_update(
        index_elements=["platform", "username"],
        set_={
            "profile_url": func.coalesce(
                stmt.excluded.profile_url, Maintainer.profile_url
            ),
            "last_active_at": func.greatest(
                Maintainer.last_active_at, stmt.excluded.last_active_at
            ),
        },
    ).returning(Maintainer.platform, Maintainer.username, Maintainer.maintainer_id)
    ids = {(pf, u): i for pf, u, i in (await session.execute(stmt)).all()}

    links = {
        (pid, ids[(m.platform, p.username)]): p.role
        for pid, m in found.items()
        for p in m.people
    }
    for pid in sorted(found):
        keep = [mid for (p, mid) in links if p == pid]
        await session.execute(
            delete(MaintainedBy).where(
                MaintainedBy.package_id == pid, MaintainedBy.maintainer_id.not_in(keep)
            )
        )
    ins = insert(MaintainedBy).values(
        [
            {"package_id": p, "maintainer_id": m, "role": r}
            for (p, m), r in sorted(links.items())
        ]
    )
    await session.execute(
        ins.on_conflict_do_update(
            index_elements=["package_id", "maintainer_id"],
            set_={"role": ins.excluded.role},
        )
    )
    return len(ids)
