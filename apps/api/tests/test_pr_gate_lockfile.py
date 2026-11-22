from pathlib import Path

import httpx
import respx

from app.services import github

REPO = "https://api.github.com/repos/acme/api-gateway"
FILES = f"{REPO}/pulls/7/files"
LOCK = (Path(__file__).parent / "fixtures" / "uv.lock").read_text()


def mock_pr(merge_base: str | None = "mb") -> None:
    respx.get(f"{REPO}/pulls/7").respond(200, json={"base": {"sha": "b"}, "head": {"sha": "h"}})
    route = respx.get(f"{REPO}/compare/b...h")
    if merge_base:
        route.respond(200, json={"merge_base_commit": {"sha": merge_base}})
    else:
        route.respond(500)


async def added() -> tuple[list, list]:
    async with httpx.AsyncClient() as c:
        return await github.pr_added_deps(c, "acme/api-gateway", 7, None)


@respx.mock
async def test_lockfile_diff_reports_only_new_production_packages():
    head = f"{REPO}/contents/uv.lock?ref=headsha"
    respx.get(FILES).respond(
        200,
        json=[
            {"filename": "uv.lock", "status": "modified", "contents_url": head},
            {"filename": "pyproject.toml", "status": "removed", "contents_url": "x"},
        ],
    )
    mock_pr()
    respx.get(head).respond(200, text=LOCK)
    base = LOCK.replace('    { name = "werkzeug" },\n', "").replace("werkzeug", "gone")
    respx.get(f"{REPO}/contents/uv.lock", params={"ref": "mb"}).respond(
        200, text=base
    )  # merge base
    deps, notes = await added()
    assert deps == [("pypi", "werkzeug", "3.0.1")]  # flask unchanged, pytest dev-only
    assert notes == []


@respx.mock
async def test_new_lockfile_and_unparseable_lockfile():
    new = f"{REPO}/contents/uv.lock?ref=h"
    bad = f"{REPO}/contents/Cargo.lock?ref=h"
    respx.get(FILES).respond(
        200,
        json=[
            {"filename": "uv.lock", "status": "added", "contents_url": new},
            {"filename": "Cargo.lock", "status": "added", "contents_url": bad},
        ],
    )
    mock_pr()
    respx.get(new).respond(200, text=LOCK)
    respx.get(bad).respond(200, text="= not toml")
    deps, notes = await added()
    assert deps == [("pypi", "flask", "3.0.0"), ("pypi", "werkzeug", "3.0.1")]
    assert notes == ["Cargo.lock: could not be parsed, not inspected"]


@respx.mock
async def test_oversized_base_is_not_treated_as_empty(monkeypatch):
    monkeypatch.setattr(github, "MAX_FILE_CHARS", len(LOCK) + 10)
    head = f"{REPO}/contents/uv.lock?ref=h"
    respx.get(FILES).respond(
        200, json=[{"filename": "uv.lock", "status": "modified", "contents_url": head}]
    )
    mock_pr()
    respx.get(head).respond(200, text=LOCK)
    respx.get(f"{REPO}/contents/uv.lock", params={"ref": "mb"}).respond(200, text=LOCK * 3)
    deps, notes = await added()
    assert deps == [] and notes == ["uv.lock: too large to inspect, not inspected"]


@respx.mock
async def test_merge_base_failure_notes_each_file_instead_of_dropping_gate():
    head = f"{REPO}/contents/uv.lock?ref=h"
    respx.get(FILES).respond(
        200, json=[{"filename": "uv.lock", "status": "modified", "contents_url": head}]
    )
    mock_pr(merge_base=None)
    deps, notes = await added()
    assert deps == [] and notes == ["uv.lock: merge base unavailable, not inspected"]


@respx.mock
async def test_full_file_inspections_are_capped(monkeypatch):
    monkeypatch.setattr(github, "MAX_FULL_FILES", 1)
    urls = [f"{REPO}/contents/{n}/uv.lock?ref=h" for n in ("a", "b")]
    respx.get(FILES).respond(
        200,
        json=[
            {"filename": f"{n}/uv.lock", "status": "added", "contents_url": u}
            for n, u in zip("ab", urls, strict=True)
        ],
    )
    mock_pr()
    respx.get(urls[0]).respond(200, text=LOCK)
    deps, notes = await added()
    assert len(deps) == 2
    assert notes == ["b/uv.lock: more than 1 lockfiles, not inspected"]


@respx.mock
async def test_added_packages_per_file_are_capped(monkeypatch):
    monkeypatch.setattr(github, "MAX_ADDED_PER_FILE", 1)
    head = f"{REPO}/contents/uv.lock?ref=h"
    respx.get(FILES).respond(
        200, json=[{"filename": "uv.lock", "status": "added", "contents_url": head}]
    )
    mock_pr()
    respx.get(head).respond(200, text=LOCK)
    deps, notes = await added()
    assert len(deps) == 1 and "only the first 1 inspected" in notes[0]


@respx.mock
async def test_poetry_lock_20_uses_sibling_pyproject():
    lock_url = f"{REPO}/contents/poetry.lock?ref=h"
    py_url = f"{REPO}/contents/pyproject.toml?ref=h"
    respx.get(FILES).respond(
        200,
        json=[
            {"filename": "poetry.lock", "status": "added", "contents_url": lock_url},
            {"filename": "pyproject.toml", "status": "modified", "contents_url": py_url},
        ],
    )
    mock_pr()
    respx.get(lock_url).respond(
        200,
        text='[[package]]\nname = "django"\nversion = "4.2.1"\n\n'
        '[[package]]\nname = "black"\nversion = "24.1.0"\n',
    )
    respx.get(py_url).respond(
        200,
        text='[tool.poetry.dependencies]\ndjango = "^4"\n[tool.poetry.group.dev.dependencies]\nblack = "*"\n',
    )
    respx.get(f"{REPO}/contents/pyproject.toml", params={"ref": "mb"}).respond(404)
    deps, _ = await added()
    assert ("pypi", "django", "4.2.1") in deps and ("pypi", "black", "24.1.0") not in deps
