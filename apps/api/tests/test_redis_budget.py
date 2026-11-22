"""Redis commands per dashboard page load (Upstash bills per command)."""

from redis.asyncio.client import Pipeline

from app.database import get_redis
from app.services import cache

PAGE = ["/me", "/stats/overview", "/services", "/vulnerabilities", "/remediation/plan/1"]


def count_commands(monkeypatch) -> list[int]:
    """Count commands this app's client sends (INFO commandstats is server-wide, so other clients
    on the same Redis would inflate it). A pipeline counts each queued command; EVAL counts once."""
    sent = [0]
    redis = get_redis()
    send, run = redis.execute_command, Pipeline.execute

    async def execute_command(*args, **kwargs):
        sent[0] += 1
        return await send(*args, **kwargs)

    async def execute(self, *args, **kwargs):
        sent[0] += len(self.command_stack)
        return await run(self, *args, **kwargs)

    monkeypatch.setattr(redis, "execute_command", execute_command)
    monkeypatch.setattr(Pipeline, "execute", execute)
    return sent


async def load(client, auth, sent: list[int]) -> int:
    before = sent[0]
    for path in PAGE:
        assert (await client.get(path, headers=auth)).status_code == 200
    return sent[0] - before


async def test_page_load_stays_within_its_redis_budget(client, auth, monkeypatch):
    """5 requests. A warm request costs the limiter's EVAL plus one GET of the cached body."""
    # a page load is one burst; stop a slow machine from expiring the in-process copies mid-burst
    monkeypatch.setattr(cache, "LOCAL_TTL", 60)
    sent = count_commands(monkeypatch)
    cold, warm = await load(client, auth, sent), await load(client, auth, sent)
    print(f"redis commands: cold={cold} warm={warm}")
    assert cold <= 16 and warm <= 9  # measured; raise only with a reason
