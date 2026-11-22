"""Realistic analysis scenarios: remediation plan, set cover vs brute force, blast-radius graph."""

import itertools
import math
import random

import pytest
from sqlalchemy import text

from app.database import SessionLocal, get_redis
from app.services import blast_radius, cache
from app.services.set_cover import greedy_set_cover
from tests.conftest import DEV_USER

# ---- set cover vs brute force ----------------------------------------------------------------


def brute_force_opt(universe: set[int], sets: dict[str, set[int]]) -> int:
    coverable = set().union(*sets.values()) & universe if sets else set()
    for k in range(len(sets) + 1):
        for combo in itertools.combinations(sets.values(), k):
            if set().union(*combo) >= coverable if combo else not coverable:
                return k
    raise AssertionError("unreachable")


@pytest.mark.parametrize("seed", range(60))
def test_greedy_within_harmonic_bound_and_coverage_exact(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 10)
    universe = set(range(n))
    sets = {
        f"p{i}": {e for e in range(n + 2) if rng.random() < 0.35}  # elements >= n are out of scope
        for i in range(rng.randint(1, 7))
    }
    plan = greedy_set_cover(universe, sets)
    coverable = set().union(*sets.values()) & universe
    opt = brute_force_opt(universe, sets)

    covered: set[int] = set()
    for step in plan:
        gain = set(step["vulns_fixed"])
        assert gain and not gain & covered  # every step adds something new, never double counts
        assert gain <= sets[step["package"]] & universe
        assert step["new_coverage"] == len(gain)
        covered |= gain
        assert step["cumulative_coverage_pct"] == round(100 * len(covered) / n, 2)
    assert covered == coverable  # greedy stops only when nothing else can be fixed
    biggest = max((len(s & universe) for s in sets.values()), default=1) or 1
    harmonic = sum(1 / i for i in range(1, biggest + 1))
    assert len(plan) <= math.ceil(harmonic * opt) and len(plan) >= opt


def test_greedy_is_deterministic_on_ties():
    sets = {2: {1, 2}, 10: {3, 4}, 9: {1, 2}}
    assert [s["package"] for s in greedy_set_cover({1, 2, 3, 4}, sets)] == [
        2,
        10,
    ]  # int order


# ---- remediation plan end to end -------------------------------------------------------------


async def mkversion(s, eco: str, name: str, version: str = "1.0.0") -> int:
    pid = (
        await s.execute(
            text(
                "INSERT INTO package (ecosystem, name) VALUES (:e, :n) "
                "ON CONFLICT (ecosystem, name) DO UPDATE SET name = EXCLUDED.name "
                "RETURNING package_id"
            ),
            {"e": eco, "n": name},
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO package_version (package_id, version_string, license_id, published_at) "
            "VALUES (:p, :v, 1, now()) ON CONFLICT DO NOTHING"
        ),
        {"p": pid, "v": version},
    )
    return pid


async def mkservice(s, team_id: int, name: str) -> int:
    sid = (
        await s.execute(
            text(
                "INSERT INTO service (team_id, name, language) VALUES (:t, :n, 'python') "
                "RETURNING service_id"
            ),
            {"t": team_id, "n": name},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO deployment (service_id, deploy_seq) VALUES (:s, 1)"),
        {"s": sid},
    )
    return sid


async def resolve(s, sid: int, pid: int, version="1.0.0", depth=0, direct=True) -> None:
    await s.execute(
        text(
            "INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth, "
            "is_direct) VALUES (:s, 1, :p, :v, :d, :i)"
        ),
        {"s": sid, "p": pid, "v": version, "d": depth, "i": direct},
    )


async def mkvuln(s, osv: str, pid: int, fixed: str | None, severity="CRITICAL", cvss=9.8) -> int:
    vid = (
        await s.execute(
            text(
                "INSERT INTO vulnerability (osv_id, severity, cvss_score) VALUES (:o, :s, :c) "
                "RETURNING vuln_id"
            ),
            {"o": osv, "s": severity, "c": cvss},
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO affects (package_id, version_string, vuln_id, fixed_in) VALUES (:p, '1.0.0', :v, :f)"
        ),
        {"p": pid, "v": vid, "f": fixed},
    )
    return vid


async def mkteam(s, name: str) -> int:
    """A team the dev-token user (the `auth` fixture) belongs to."""
    team = (
        await s.execute(
            text("INSERT INTO team (name, slug) VALUES (:n, :n) RETURNING team_id"),
            {"n": name},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO team_member (team_id, user_id) VALUES (:t, :u)"),
        {"t": team, "u": DEV_USER},
    )
    return team


async def test_plan_counts_service_package_vuln_elements(client, auth, reseed):
    """Same vuln in two services via different packages; only P is fixable -> 1 step, 50%."""
    async with SessionLocal() as s, s.begin():
        team = await mkteam(s, "plan-team")
        s1, s2 = await mkservice(s, team, "svc-a"), await mkservice(s, team, "svc-b")
        p = await mkversion(s, "pypi", "plan-p")
        q = await mkversion(s, "pypi", "plan-q")
        vid = await mkvuln(s, "PLAN-1", p, fixed="1.0.1")
        await s.execute(
            text(
                "INSERT INTO affects (package_id, version_string, vuln_id) VALUES (:p, '1.0.0', :v)"
            ),
            {"p": q, "v": vid},
        )
        await resolve(s, s1, p)
        await resolve(s, s2, q)
    steps = (await client.get(f"/remediation/plan/{team}", headers=auth)).json()["data"]
    assert len(steps) == 1
    assert steps[0]["package"] == "plan-p" and steps[0]["services_affected"] == ["svc-a"]
    assert steps[0]["new_coverage"] == 1 and steps[0]["cumulative_coverage_pct"] == 50.0


async def test_plan_ranks_shared_package_first(client, auth, reseed):
    """A package vulnerable in 2 services outranks one vulnerable in 1; coverage adds to 100%."""
    async with SessionLocal() as s, s.begin():
        team = await mkteam(s, "rank-team")
        s1, s2 = await mkservice(s, team, "r-a"), await mkservice(s, team, "r-b")
        shared = await mkversion(s, "pypi", "rank-shared")
        solo = await mkversion(s, "pypi", "rank-solo")
        await mkvuln(s, "RANK-1", shared, fixed="2.0.0")
        await mkvuln(s, "RANK-2", solo, fixed="2.0.0")
        for sid in (s1, s2):
            await resolve(s, sid, shared)
        await resolve(s, s1, solo)
    steps = (await client.get(f"/remediation/plan/{team}", headers=auth)).json()["data"]
    assert [x["package"] for x in steps] == ["rank-shared", "rank-solo"]
    assert [x["new_coverage"] for x in steps] == [2, 1]
    assert [x["cumulative_coverage_pct"] for x in steps] == [66.67, 100.0]


# ---- blast radius on a hand-built diamond ----------------------------------------------------
#   direct A -> B -> D -> E (vulnerable)        F: resolved but unreachable from any direct dep
#            \-> C -/


def node(pid, name, direct=False, depth=1):
    return {
        "package_id": pid,
        "name": name,
        "version": "1",
        "is_direct": direct,
        "depth": depth,
    }


def edge(a, b):
    return {
        "dep_package_id": a,
        "dep_version_string": "1",
        "ref_package_id": b,
        "ref_version_string": "1",
    }


def test_shortest_paths_diamond():
    nodes = [node(1, "A", True, 0), node(2, "B"), node(3, "C"), node(4, "D", depth=2),
             node(5, "E", depth=3), node(6, "F", depth=1), node(7, "Z", True, 0)]  # fmt: skip
    edges = [edge(1, 2), edge(1, 3), edge(2, 4), edge(3, 4), edge(4, 5), edge(6, 5)]
    paths = blast_radius.shortest_paths(nodes, edges)
    assert paths[(1, "1")] == ["A@1"] and paths[(7, "1")] == ["Z@1"]
    assert paths[(4, "1")] in (["A@1", "B@1", "D@1"], ["A@1", "C@1", "D@1"])
    assert paths[(5, "1")][:1] == ["A@1"] and paths[(5, "1")][-2:] == ["D@1", "E@1"]
    assert len(paths[(5, "1")]) == 4  # shortest: the diamond is not walked twice
    assert (
        6,
        "1",
    ) not in paths  # no path from a direct dependency (F only points at E)


async def test_blast_radius_path_and_cache_generation(client, auth, reseed):
    async with SessionLocal() as s, s.begin():
        team = await mkteam(s, "diamond-team")
        sid = await mkservice(s, team, "diamond")
        ids = {n: await mkversion(s, "pypi", f"dia-{n}") for n in "abcde"}
        for n, depth in zip("abcde", (0, 1, 1, 2, 3)):
            await resolve(s, sid, ids[n], depth=depth, direct=(n == "a"))
        for x, y in ("ab", "ac", "bd", "cd", "de"):
            await s.execute(
                text(
                    "INSERT INTO depends_on (dep_package_id, dep_version_string, ref_package_id, "
                    "ref_version_string, scope) VALUES (:a, '1.0.0', :b, '1.0.0', 'runtime')"
                ),
                {"a": ids[x], "b": ids[y]},
            )
        await mkvuln(s, "DIA-1", ids["e"], fixed=None)

    first = (await client.get(f"/blast-radius/{sid}", headers=auth)).json()["data"]
    assert first["cached"] is False and first["total_vulns"] == 1 and first["critical_count"] == 1
    hit = first["vulnerable_packages"][0]
    assert hit["name"] == "dia-e" and hit["depth"] == 3
    assert hit["path"][0] == "dia-a@1.0.0" and hit["path"][-2:] == [
        "dia-d@1.0.0",
        "dia-e@1.0.0",
    ]
    assert len(hit["path"]) == 4
    assert (await client.get(f"/blast-radius/{sid}", headers=auth)).json()["data"]["cached"] is True

    await cache.bump()
    again = (await client.get(f"/blast-radius/{sid}", headers=auth)).json()["data"]
    assert again["cached"] is False  # generation moved on
    # a reader that computed before the bump writes to its OLD generation: never served
    await get_redis().set(
        f"{cache.PREFIX}0:blast_radius:{sid}", '{"service_id": 0, "poison": true}'
    )
    assert "poison" not in (await client.get(f"/blast-radius/{sid}", headers=auth)).json()["data"]
