"""GitHub App auth, PR file diffs -> added deps, Check Runs."""

import base64
import re
import time

import httpx
import jwt
import structlog

from app.config import settings
from app.services import http
from app.services.manifest_parser import LOCKFILES, parse_manifest

log = structlog.get_logger()
API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
MANIFESTS = {"package.json": "npm", "cargo.toml": "cargo", "go.mod": "go"}
# manifests/lockfiles whose changes we cannot read: the gate says so instead of passing silently
UNPARSED = {"pipfile", "pipfile.lock", "npm-shrinkwrap.json", "go.sum"}
# Files whose '+' lines cannot be parsed alone: diffed as head vs base instead.
FULL_FILE_ECOSYSTEM = {"pyproject.toml": "pypi", **{n: eco for n, (eco, _) in LOCKFILES.items()}}
RAW = {"Accept": "application/vnd.github.raw+json"}
MAX_FILE_CHARS = 1_000_000  # same cap as the ingest upload
MAX_FULL_FILES = 10  # whole-file inspections per PR
MAX_ADDED_PER_FILE = 500
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
    except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError) as e:
        log.warning("installation_token_failed", installation=installation_id, error=str(e))
        return None


async def repo_access(client: httpx.AsyncClient, full_name: str) -> tuple[int, int] | None:
    """(installation id, repository id) when the App is installed on `full_name`, else None.

    Raises httpx.HTTPError when GitHub is unreachable or the installation token cannot be minted.
    """
    r = await http.request(
        client,
        "GET",
        f"{API}/repos/{full_name}/installation",
        headers={**HEADERS, "Authorization": f"Bearer {app_jwt()}"},
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    installation = int(r.json()["id"])
    token = await installation_token(client, installation)
    if token is None:
        raise httpx.HTTPError("GitHub installation token unavailable")
    repo = await http.request(
        client,
        "GET",
        f"{API}/repos/{full_name}",
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
    )
    repo.raise_for_status()
    return installation, int(repo.json()["id"])


def ecosystem_of(filename: str) -> str | None:
    base = filename.rsplit("/", 1)[-1].lower()
    if base.startswith("requirements") and base.endswith(".txt"):
        return "pypi"
    return MANIFESTS.get(base)


def _sibling(pyproject: str, lock: str) -> bool:
    d = lock.rpartition("/")[0]
    return lock.lower().endswith("poetry.lock") and pyproject == f"{d}/pyproject.toml".lstrip("/")


class TooLarge(Exception):
    """A file over MAX_FILE_CHARS: unlike an absent file, it cannot be treated as empty."""


async def _file_at(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], **params: str
) -> str | None:
    """Raw file text, None when absent (404); raises TooLarge over the size cap."""
    r = await http.request(client, "GET", url, headers={**headers, **RAW}, params=params)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    if len(r.text) > MAX_FILE_CHARS:
        raise TooLarge
    return r.text


async def changed_full_file_deps(
    client: httpx.AsyncClient,
    full_name: str,
    f: dict,
    base_sha: str,
    headers: dict[str, str],
    pyproject_url: str | None = None,
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """(deps, notes): (ecosystem, name, version) in the head lockfile/pyproject but not the base."""
    filename = f["filename"]
    eco = FULL_FILE_ECOSYSTEM[filename.rsplit("/", 1)[-1].lower()]
    try:
        head = await _file_at(client, f["contents_url"], headers)
        pyproject = await _file_at(client, pyproject_url, headers) if pyproject_url else None
        old_name = f.get("previous_filename", filename)
        base = (
            None
            if f["status"] == "added"
            else await _file_at(
                client, f"{API}/repos/{full_name}/contents/{old_name}", headers, ref=base_sha
            )
        )
    except TooLarge:
        return [], [f"{filename}: too large to inspect, not inspected"]
    except httpx.HTTPError as e:  # one unreadable file must not sink the whole gate
        log.warning("pr_lockfile_unreadable", file=filename, error=str(e))
        return [], [f"{filename}: could not be fetched, not inspected"]
    if head is None:
        return [], [f"{filename}: could not be fetched, not inspected"]
    try:
        new = {
            (p.name, p.version) for p in parse_manifest(eco, head, filename, pyproject=pyproject)
        }
        old = (
            {(p.name, p.version) for p in parse_manifest(eco, base, filename, pyproject=pyproject)}
            if base
            else set()
        )
    except ValueError as e:
        log.warning("pr_manifest_unparseable", file=filename, error=str(e))
        return [], [f"{filename}: could not be parsed, not inspected"]
    added = [(eco, n, v) for n, v in sorted(new - old)]
    if len(added) > MAX_ADDED_PER_FILE:
        return added[:MAX_ADDED_PER_FILE], [
            f"{filename}: {len(added)} new packages, only the first {MAX_ADDED_PER_FILE} inspected"
        ]
    return added, []


def added_deps(filename: str, patch: str) -> list[tuple[str, str, str]]:
    """(ecosystem, name, version) from a diff's '+' lines.

    shortcut: parses added lines in isolation (npm: line-wise JSON, so non-dependency
    `"key": "value"` lines parse too and simply miss the DB). Lockfiles and pyproject.toml are
    diffed whole in `changed_full_file_deps` instead.
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


async def _merge_base(
    client: httpx.AsyncClient, full_name: str, number: int, headers: dict[str, str]
) -> str | None:
    """Merge-base commit of the PR (what GitHub's own diff is against); None (logged) on failure."""
    try:
        pr = await http.request(
            client, "GET", f"{API}/repos/{full_name}/pulls/{number}", headers=headers
        )
        pr.raise_for_status()
        base, head = pr.json()["base"]["sha"], pr.json()["head"]["sha"]
        cmp = await http.request(
            client, "GET", f"{API}/repos/{full_name}/compare/{base}...{head}", headers=headers
        )
        cmp.raise_for_status()
        return cmp.json()["merge_base_commit"]["sha"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("pr_merge_base_failed", pr=number, error=str(e))
        return None


async def pr_added_deps(
    client: httpx.AsyncClient, full_name: str, number: int, token: str | None
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """(added deps, notes). A note names a changed manifest we could not inspect (missing patch,
    pyproject/lockfile): the caller must not let such a PR pass silently."""
    headers = {**HEADERS, **({"Authorization": f"Bearer {token}"} if token else {})}
    deps: list[tuple[str, str, str]] = []
    notes: list[str] = []
    full: list[dict] = []
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
            if base in FULL_FILE_ECOSYSTEM:
                full.append(f)
            elif base in UNPARSED:
                notes.append(f"{name}: {base} changes are not inspected")
            elif ecosystem_of(name) and patch is None and f.get("changes", 1) > 0:
                notes.append(f"{name}: diff too large for GitHub to include, not inspected")
            else:
                deps.extend(added_deps(name, patch or ""))
        if len(batch) < FILES_PER_PAGE:
            break
    else:
        notes.append(f"PR touches {MAX_PR_FILES}+ files: files beyond GitHub's cap not inspected")
    if full:  # the merge base is only needed for these, so the extra calls are skipped otherwise
        base_sha = await _merge_base(client, full_name, number, headers)
        for f in full[:MAX_FULL_FILES]:
            if base_sha is None:
                notes.append(f"{f['filename']}: merge base unavailable, not inspected")
                continue
            sibling = next(  # poetry.lock 2.0 needs its pyproject.toml to tell dev from main
                (g["contents_url"] for g in full if _sibling(g["filename"], f["filename"])), None
            )
            found, note = await changed_full_file_deps(
                client, full_name, f, base_sha, headers, sibling
            )
            deps += found
            notes += note
        for f in full[MAX_FULL_FILES:]:
            notes.append(f"{f['filename']}: more than {MAX_FULL_FILES} lockfiles, not inspected")
    return list(dict.fromkeys(deps)), notes


async def post_check_run(
    client: httpx.AsyncClient, full_name: str, head_sha: str, token: str, verdict: str, summary: str
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
