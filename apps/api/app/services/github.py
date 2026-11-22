"""GitHub App auth, PR file diffs -> added deps, Check Runs."""

import base64
import re
import time

import httpx
import structlog
from jose import JOSEError, jwt

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
# manifests/lockfiles whose changes we cannot read: the gate says so instead of passing silently
UNPARSED = {
    "pyproject.toml", "pipfile", "pipfile.lock", "poetry.lock", "uv.lock", "package-lock.json",
    "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock", "go.sum",
}  # fmt: skip
FILES_PER_PAGE = 100
MAX_PR_FILES = 3000  # GitHub's cap on the PR files API
NPM_LINE = re.compile(r'^\s*"[^"]+"\s*:\s*"[^"]*"\s*,?\s*$')


def configured() -> bool:
    return bool(settings.github_app_id and settings.github_private_key_base64)


def app_jwt() -> str:
    pem = base64.b64decode(settings.github_private_key_base64).decode()
    now = int(time.time())
    claims = {"iat": now - 60, "exp": now + 540, "iss": settings.github_app_id}
    return jwt.encode(claims, pem, algorithm="RS256")


async def installation_token(client: httpx.AsyncClient, installation_id: int) -> str | None:
    """None (logged) when minting fails: public repos still get evaluated, just no Check Run."""
    try:
        r = await http.request(
            client,
            "POST",
            f"{API}/app/installations/{installation_id}/access_tokens",
            headers={**HEADERS, "Authorization": f"Bearer {app_jwt()}"},
        )
        r.raise_for_status()
        return r.json()["token"]
    except (httpx.HTTPError, JOSEError, ValueError, KeyError) as e:
        log.warning("installation_token_failed", installation=installation_id, error=str(e))
        return None


def ecosystem_of(filename: str) -> str | None:
    base = filename.rsplit("/", 1)[-1].lower()
    if base.startswith("requirements") and base.endswith(".txt"):
        return "pypi"
    return MANIFESTS.get(base)


def added_deps(filename: str, patch: str) -> list[tuple[str, str, str]]:
    """(ecosystem, name, version) from a diff's '+' lines.

    shortcut: parses added lines in isolation (npm: line-wise JSON, so non-dependency
    `"key": "value"` lines parse too and simply miss the DB); pyproject.toml and lockfiles are
    reported as not inspected by pr_added_deps; upgrade to fetching the full head file.
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
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """(added deps, notes). A note names a changed manifest we could not inspect (missing patch,
    pyproject/lockfile): the caller must not let such a PR pass silently."""
    headers = {**HEADERS, **({"Authorization": f"Bearer {token}"} if token else {})}
    deps: list[tuple[str, str, str]] = []
    notes: list[str] = []
    for page in range(1, MAX_PR_FILES // FILES_PER_PAGE + 1):
        r = await http.request(
            client,
            "GET",
            f"{API}/repos/{full_name}/pulls/{number}/files",
            headers=headers,
            params={"per_page": FILES_PER_PAGE, "page": page},
        )
        r.raise_for_status()
        batch = r.json()
        for f in batch:
            name, patch = f["filename"], f.get("patch")
            if f.get("status") == "removed":
                continue
            base = name.rsplit("/", 1)[-1].lower()
            if base in UNPARSED:
                notes.append(f"{name}: {base} changes are not inspected")
            elif ecosystem_of(name) and patch is None and f.get("changes", 1) > 0:
                notes.append(f"{name}: diff too large for GitHub to include, not inspected")
            else:
                deps.extend(added_deps(name, patch or ""))
        if len(batch) < FILES_PER_PAGE:
            break
    else:
        notes.append(f"PR touches {MAX_PR_FILES}+ files: files beyond GitHub's cap not inspected")
    return list(dict.fromkeys(deps)), notes


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
