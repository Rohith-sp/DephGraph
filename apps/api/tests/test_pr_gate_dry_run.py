"""scripts/pr_gate_dry_run.py: the PR gate without GitHub (conftest puts scripts/ on sys.path)."""

import argparse
import json

import pr_gate_dry_run as dr
import pytest
import respx
from sqlalchemy import text

from app import database
from app.config import settings
from app.database import SessionLocal, get_mongo
from app.services import pr_gate_engine

GROQ = "https://api.groq.com/openai/v1/chat/completions"


@pytest.fixture(autouse=True)
def _no_groq(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")


async def _count(pr: int) -> int:
    async with SessionLocal() as s:
        return (
            await s.execute(
                text("SELECT COUNT(*) FROM pr_review WHERE service_id = 1 AND pr_number = :n"),
                {"n": pr},
            )
        ).scalar_one()


def test_parse_dep():
    assert dr.parse_dep("PyPI:httpx:0.27.0") == ("pypi", "httpx", "0.27.0")
    assert dr.parse_dep("pypi:Flask_Login:0.6") == ("pypi", "flask-login", "0.6")  # PEP 503
    assert dr.parse_dep("npm:Flask_Login:0.6") == ("npm", "Flask_Login", "0.6")
    assert dr.parse_dep("go:github.com/a/b:v1.0.0+x:y") == ("go", "github.com/a/b", "v1.0.0+x:y")
    for bad in ("httpx", "pypi:httpx", "pypi::1.0"):
        with pytest.raises(argparse.ArgumentTypeError):
            dr.parse_dep(bad)


async def test_dry_run_read_only_then_save(reseed):
    deps = [("pypi", "unknownpkg", "1.0.0"), ("npm", "lodash", "4.17.15")]
    async with SessionLocal() as s:
        report = await dr.dry_run(s, 1, deps, pr_number=4242)
    assert report["service_id"] == 1 and report["saved"] is False and report["groq"] is False
    assert [d["name"] for d in report["deps"]] == ["unknownpkg", "lodash"]
    first = report["deps"][0]  # not in the DB and live lookup off: unverified, never PASS
    assert (first["verdict"], first["stage_reached"]) == ("ADVISORY", 2)
    assert first["reasoning"].startswith("could not verify unknownpkg: ")
    assert report["verdict"] in ("PASS", "ADVISORY", "BLOCK")
    assert await _count(4242) == 0  # nothing written

    table = dr.format_table(report)
    assert "pypi:unknownpkg==1.0.0" in table and "overall:" in table and "not saved" in table

    async with SessionLocal() as s:
        saved = await dr.dry_run(s, 1, deps, pr_number=4242, save=True)
    assert saved["saved"] is True and await _count(4242) == 1
    assert "saved as PR #4242" in dr.format_table(saved)


async def test_dry_run_unknown_service():
    async with SessionLocal() as s:
        with pytest.raises(LookupError):
            await dr.dry_run(s, 999999, [("pypi", "x", "1")])


@respx.mock
async def test_dry_run_stage3_does_not_log_to_mongo(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "gsk_test")
    content = json.dumps({"verdict": "BLOCK", "reasoning": "llm says no"})
    respx.post(GROQ).respond(
        200,
        json={
            "id": "1",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
        },
    )
    dep = pr_gate_engine.DepVerdict("advdep", "1", "ADVISORY", 2, "r")
    assert await pr_gate_engine.refine([dep], 1, 5151, store_responses=False) == ("BLOCK", 3)
    assert await get_mongo()["review_responses"].count_documents({"pr_number": 5151}) == 0
    await pr_gate_engine.close_groq()


async def test_main_json_and_errors(monkeypatch, capsys):
    async def noop() -> None:
        return None

    monkeypatch.setattr(database, "close_all", noop)  # keep the shared test engine open
    assert await dr.main(["--service", "1", "--dep", "pypi:unknownpkg:1.0.0", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "ADVISORY" and out["deps"][0]["stage_reached"] == 2
    assert await dr.main(["--service", "999999", "--dep", "pypi:x:1"]) == 2
    assert "not found" in capsys.readouterr().err
