"""GitHub App auth, PR file diffs -> added deps, Check Runs."""

import base64
import re
import time

import httpx
import structlog
from jose import jwt

from app.config import settings
from app.services import http
from app.services.manifest_parser import parse_manifest

log = structlog.get_logger()
API = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
MANIFESTS = {"package.json": "npm", "cargo.toml": "cargo", "go.mod": "go"}
NPM_LINE = re.compile(r'^\s*"[^"]+"\s*:\s*"[^"]*"\s*,?\s*$')


def configured() -> bool:
    return bool(settings.github_app_id and settings.github_private_key_base64)


def app_jwt() -> str:
    pem = base64.b64decode(settings.github_private_key_base64).decode()
    now = int(time.time())
    claims = {"iat": now - 60, "exp": now + 540, "iss": settings.github_app_id}
    return jwt.encode(claims, pem, algorithm="RS256")


async def installation_token(client: httpx.AsyncClient, installation_id: int) -> str:
    r = await http.request(
        client,
        "POST",
        f"{API}/app/installations/{installation_id}/access_tokens",
        headers={**HEADERS, "Authorization": f"Bearer {app_jwt()}"},
    )
    r.raise_for_status()
    return r.json()["token"]


def ecosystem_of(filename: str) -> str | None:
    base = filename.rsplit("/", 1)[-1].lower()
    if base.startswith("requirements") and base.endswith(".txt"):
        return "pypi"
    return MANIFESTS.get(base)


def added_deps(filename: str, patch: str) -> list[tuple[str, str, str]]:
    """(ecosystem, name, version) from a diff's '+' lines.

    shortcut: parses added lines in isolation (npm: line-wise JSON, so non-dependency
    `"key": "value"` lines parse too and simply miss the DB), no pyproject.toml; upgrade to
    fetching the full head file if that matters.
    """
    eco = ecosystem_of(filename)
    if eco is None:
        return []
    lines = [ln[1:] for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    body = {
        "pypi": "\n".join(lines),
        "npm": '{"dependencies":{'
        + ",".join(ln.strip().rstrip(",") for ln in lines if NPM_LINE.match(ln))
        + "}}",
        "cargo": "[dependencies]\n" + "\n".join(lines),
        "go": "require (\n" + "\n".join(lines) + "\n)",
    }[eco]
    try:
        return [(eco, p.name, p.version) for p in parse_manifest(eco, body)]
    except ValueError as e:
        log.warning("pr_manifest_unparseable", file=filename, error=str(e))
        return []


async def pr_added_deps(
    client: httpx.AsyncClient, full_name: str, number: int, token: str | None
) -> list[tuple[str, str, str]]:
    headers = {**HEADERS, **({"Authorization": f"Bearer {token}"} if token else {})}
    r = await http.request(
        client,
        "GET",
        f"{API}/repos/{full_name}/pulls/{number}/files",
        headers=headers,
        params={"per_page": 100},  # shortcut: first 100 files only
    )
    r.raise_for_status()
    deps = [d for f in r.json() for d in added_deps(f["filename"], f.get("patch", ""))]
    return list(dict.fromkeys(deps))


async def post_check_run(
    client: httpx.AsyncClient,
    full_name: str,
    head_sha: str,
    token: str,
    verdict: str,
    summary: str,
) -> None:
    conclusion = {"PASS": "success", "ADVISORY": "neutral", "BLOCK": "failure"}[verdict]
    r = await http.request(
        client,
        "POST",
        f"{API}/repos/{full_name}/check-runs",
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
        json={
            "name": "DephGraph PR Gate",
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": {"title": f"DephGraph: {verdict}", "summary": summary[:60000]},
        },
    )
    r.raise_for_status()
