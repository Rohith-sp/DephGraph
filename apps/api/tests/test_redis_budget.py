"""Redis commands per dashboard page load (Upstash bills per command)."""

from app.database import get_redis

PAGE = ["/me", "/stats/overview", "/services", "/vulnerabilities", "/remediation/plan/1"]


async def commands() -> int:
    stats = await get_redis().info("commandstats")
    return sum(v["calls"] for k, v in stats.items() if k != "cmdstat_info")


async def load(client, auth) -> int:
    before = await commands()
    for path in PAGE:
        assert (await client.get(path, headers=auth)).status_code == 200
    return await commands() - before


async def test_page_load_stays_within_its_redis_budget(client, auth):
    """5 requests, commands as Redis counts them (the limiter's EVAL runs INCR/EXPIRE inside).

    Before the per-team generations + in-process copies: 34 cold, 28 warm (per request two reads
    for the membership and two for the cache lookup). Now a warm request costs the limiter's two
    commands plus one GET of the cached body."""
    cold, warm = await load(client, auth), await load(client, auth)
    print(f"redis commands: cold={cold} warm={warm}")
    assert cold <= 22 and warm <= 14
