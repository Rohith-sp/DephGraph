"""Parse dependency manifests into (name, version, is_direct) rows."""

import json
import re
import tomllib
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass

import structlog
import yaml
from packaging.requirements import InvalidRequirement, Requirement

log = structlog.get_logger()

MAX_NAME, MAX_VERSION = 255, 100  # package.name / version_string column widths
MAX_PACKAGES = 20_000  # a 1 MB lockfile holds at most a few tens of thousands of entries


@dataclass(frozen=True)
class ParsedPackage:
    name: str
    version: str
    is_direct: bool = True
    deps: tuple[tuple[str, str], ...] = ()  # (name, version) edges, lockfiles only


def normalize_pypi(name: str) -> str:
    """PEP 503 name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(content: str) -> list[ParsedPackage]:
    out: list[ParsedPackage] = []
    for raw in content.replace("\r\n", "\n").replace("\\\n", " ").splitlines():
        line = re.split(r"\s+#|\s--", raw.strip(), maxsplit=1)[0].strip()
        if not line or line.startswith(("#", "-")):  # comments, -r/-e/--index-url
            continue
        try:
            req = Requirement(line)
        except InvalidRequirement:
            log.warning("requirement_unparseable", line=line)
            continue
        specs = {s.operator: s.version for s in req.specifier}
        pinned = specs.get("==") or specs.get("===")
        version = (
            pinned or specs.get(">=") or specs.get("~=") or specs.get(">") or ""
        ).removesuffix(".*")
        if not version:
            log.warning("requirement_unversioned", name=req.name)
            continue
        out.append(ParsedPackage(normalize_pypi(req.name), version))
    return out


def _clean_range(spec: str) -> str | None:
    """'^1.2.3', '>=1.0 <2', '1.x' -> first concrete version, else None."""
    tok = re.sub(r"^[\^~>=<v\s]+", "", spec.strip()).split(",")[0].split()
    ver = re.sub(r"\.[xX*]", ".0", tok[0]) if tok else ""
    return ver if re.match(r"\d", ver) else None


def parse_package_json(content: str) -> list[ParsedPackage]:
    doc = json.loads(content)
    out = []
    for section in ("dependencies", "devDependencies"):
        for name, spec in (doc.get(section) or {}).items():
            ver = _clean_range(str(spec))
            if ver is None:
                log.warning("npm_unversioned", name=name, spec=spec)
                continue
            out.append(ParsedPackage(name, ver))
    return out


def parse_cargo(content: str) -> list[ParsedPackage]:
    doc = tomllib.loads(content)
    out = []
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        for name, spec in (doc.get(section) or {}).items():
            raw = spec if isinstance(spec, str) else spec.get("version")
            ver = _clean_range(raw) if raw else None
            if ver is None:  # path/git deps carry no registry version
                log.warning("cargo_unversioned", name=name)
                continue
            real = name if isinstance(spec, str) else spec.get("package", name)
            out.append(ParsedPackage(real, ver))
    return out


def parse_go_mod(content: str) -> list[ParsedPackage]:
    out = []
    in_block = False
    for raw in content.splitlines():
        code, _, comment = raw.partition("//")
        line = code.strip()
        if line == "require (":
            in_block = True
        elif in_block and line == ")":
            in_block = False
        elif in_block or line.startswith("require "):
            parts = line.removeprefix("require ").split()
            if len(parts) == 2:
                out.append(ParsedPackage(parts[0], parts[1], "indirect" not in comment))
    return out


def _walk[K: Hashable](start: Iterable[K], children: Callable[[K], Iterable[K]]) -> set[K]:
    """Everything reachable from `start` (inclusive) in a dependency graph."""
    seen: set[K] = set()
    todo = list(start)
    while todo:
        node = todo.pop()
        if node not in seen:
            seen.add(node)
            todo.extend(children(node))
    return seen


def _registry_version(ver: str) -> bool:
    """False for link:/file:/git/alias specifiers, which carry no registry version."""
    return bool(re.match(r"\d", ver)) and not re.search(r"[:/]", ver)


def parse_pyproject(content: str) -> list[ParsedPackage]:
    """PEP 621 `dependencies` + Poetry main group; optional extras and dev groups are ignored."""
    doc = tomllib.loads(content)
    out = parse_requirements("\n".join(doc.get("project", {}).get("dependencies") or []))
    poetry = doc.get("tool", {}).get("poetry", {})
    main = {**(poetry.get("dependencies") or {})}
    main.update(poetry.get("group", {}).get("main", {}).get("dependencies") or {})
    for name, spec in main.items():
        if name.lower() == "python":
            continue
        if isinstance(spec, dict):
            spec = spec.get("version")
        ver = _clean_range(spec) if isinstance(spec, str) else None
        if ver is None:
            log.warning("poetry_unversioned", name=name)
            continue
        out.append(ParsedPackage(normalize_pypi(name), ver))
    return out


def parse_uv_lock(content: str) -> list[ParsedPackage]:
    pkgs = tomllib.loads(content).get("package") or []
    by_name: dict[str, list[int]] = {}
    for i, p in enumerate(pkgs):
        by_name.setdefault(p["name"], []).append(i)

    def match(d: dict) -> list[int]:
        return [
            j
            for j in by_name.get(d["name"], [])
            if "version" not in d or pkgs[j].get("version") == d["version"]
        ]

    wanted: dict[int, set[str]] = {}  # extras any package asks of another: httpx[http2]
    for p in pkgs:
        for d in p.get("dependencies") or []:
            for j in match(d):
                wanted.setdefault(j, set()).update(d.get("extra") or [])

    def deps(i: int) -> list[int]:
        optional = pkgs[i].get("optional-dependencies") or {}
        extra = [d for x in wanted.get(i, ()) for d in optional.get(x, [])]
        return [j for d in [*(pkgs[i].get("dependencies") or []), *extra] for j in match(d)]

    def registry(i: int) -> bool:
        return "registry" in pkgs[i].get("source", {})

    direct = {j for i in range(len(pkgs)) if not registry(i) for j in deps(i)}  # project's own deps
    prod = _walk(direct, deps)  # dev-dependencies sit under their own key, so are never reached
    return [
        ParsedPackage(
            normalize_pypi(pkgs[i]["name"]),
            pkgs[i]["version"],
            i in direct,
            tuple(
                (normalize_pypi(pkgs[j]["name"]), pkgs[j]["version"])
                for j in deps(i)
                if registry(j)
            ),
        )
        for i in sorted(prod)
        if registry(i)
    ]


def _poetry_main_names(pyproject: str) -> set[str]:
    doc = tomllib.loads(pyproject)
    names = {
        normalize_pypi(Requirement(r).name)
        for r in doc.get("project", {}).get("dependencies") or []
    }
    poetry = doc.get("tool", {}).get("poetry", {})
    main = {**(poetry.get("dependencies") or {})}
    main.update(poetry.get("group", {}).get("main", {}).get("dependencies") or {})
    return names | {normalize_pypi(n) for n in main if n.lower() != "python"}


def parse_poetry_lock(content: str, pyproject: str | None = None) -> list[ParsedPackage]:
    """Dev packages are told apart by `category`/`groups` (lock-version < 2.0 or >= 2.1); a 2.0
    lock has neither, so the sibling pyproject.toml's main dependencies decide, if given."""
    pkgs = tomllib.loads(content).get("package") or []
    pkgs = [p for p in pkgs if p.get("source", {}).get("type", "legacy") == "legacy"]
    by_name = {normalize_pypi(p["name"]): p for p in pkgs}  # one version per name

    def children(n: str) -> list[str]:
        return [
            d for d in map(normalize_pypi, by_name[n].get("dependencies") or {}) if d in by_name
        ]

    direct: set[str] | None = None
    if any("category" in p or "groups" in p for p in pkgs):
        keep = {
            n
            for n, p in by_name.items()
            if p.get("category", "main") == "main" and "main" in p.get("groups", ["main"])
        }
    elif pyproject is not None:
        direct = _poetry_main_names(pyproject) & by_name.keys()
        keep = _walk(direct, children)
    else:
        # shortcut: a lock-version 2.0 poetry.lock alone cannot tell dev from main, so dev
        # packages are included; upgrade by ingesting pyproject.toml alongside.
        log.warning("poetry_lock_dev_unknown")
        keep = set(by_name)
    # poetry.lock does not mark direct deps, so without a pyproject all count as direct
    return [
        ParsedPackage(
            n,
            by_name[n]["version"],
            direct is None or n in direct,
            tuple((c, by_name[c]["version"]) for c in children(n) if c in keep),
        )
        for n in by_name
        if n in keep
    ]


def parse_cargo_lock(content: str) -> list[ParsedPackage]:
    pkgs = tomllib.loads(content).get("package") or []
    versions: dict[str, list[str]] = {}
    for p in pkgs:
        versions.setdefault(p["name"], []).append(p["version"])

    def edges(p: dict) -> tuple[tuple[str, str], ...]:
        out = []
        for d in p.get("dependencies") or []:  # "name", "name 1.2.3" or "name 1.2.3 (source)"
            name, *rest = d.split()
            ver = rest[0] if rest else versions.get(name, [""])[0]
            if ver in versions.get(name, ()):
                out.append((name, ver))
        return tuple(out)

    direct = {n for p in pkgs if "source" not in p for n, _ in edges(p)}  # workspace members
    # Cargo.lock does not separate dev-dependencies, so those of workspace members are included
    return [
        ParsedPackage(p["name"], p["version"], p["name"] in direct, edges(p))
        for p in pkgs
        if p.get("source", "").startswith("registry+")
    ]


def _npm_registry(e: dict) -> bool:
    """False for git/file/tarball installs, which carry no registry version."""
    resolved = e.get("resolved")
    return resolved is None or (
        resolved.startswith(("http://", "https://")) and "github.com" not in resolved
    )


def parse_package_lock(content: str) -> list[ParsedPackage]:
    doc = json.loads(content)
    entries = doc.get("packages")
    if not isinstance(entries, dict):
        raise ValueError("package-lock.json needs lockfileVersion 2 or 3")
    # the root and every workspace member (a path outside node_modules) are the project's own
    roots = [e for path, e in entries.items() if "node_modules/" not in path and not e.get("link")]
    wanted = {
        n
        for r in roots
        for section in ("dependencies", "optionalDependencies")
        for n in r.get(section) or {}
    }

    def edges(path: str, e: dict) -> tuple[tuple[str, str], ...]:
        out = []
        for dep in {**(e.get("dependencies") or {}), **(e.get("optionalDependencies") or {})}:
            cur = path  # node resolution: nearest node_modules/<dep> walking up
            while True:
                hit = entries.get(f"{cur}/node_modules/{dep}".lstrip("/"))
                if hit or not cur:
                    break
                cur = cur.rpartition("/node_modules/")[0] if "/node_modules/" in cur else ""
            if hit and hit.get("version"):
                out.append((hit.get("name", dep), hit["version"]))
        return tuple(out)

    out = []
    for path, e in entries.items():
        if "node_modules/" not in path or e.get("dev") or e.get("link"):
            continue
        if not e.get("version") or not _npm_registry(e):
            continue
        key = path.rsplit("node_modules/", 1)[-1]  # the alias when installed as npm:real@x
        direct = path == f"node_modules/{key}" and key in wanted
        out.append(ParsedPackage(e.get("name", key), e["version"], direct, edges(path, e)))
    return out


def _pnpm_key(name: str, ver: object) -> str:
    """Snapshot/package key of a dependency entry: a plain version, or an alias like 'bar@1.0.0'."""
    ver = str(ver).lstrip("/")
    return ver if "@" in ver.split("(")[0] else f"{name}@{ver}"


def _pnpm_split(key: str) -> tuple[str, str]:
    """'/@a/b@1.0.0(peer@2)' or 'b@1.0.0' -> ('@a/b', '1.0.0')."""
    spec = key.lstrip("/").split("(")[0]
    name, _, ver = spec.rpartition("@")
    return name, ver


def _pnpm_children(entry: dict | None) -> list[str]:
    entry = entry or {}
    deps = {**(entry.get("optionalDependencies") or {}), **(entry.get("dependencies") or {})}
    keys = [_pnpm_key(n, v) for n, v in deps.items()]
    return [k for k in keys if _registry_version(_pnpm_split(k)[1])]


class _NoAliasLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):  # type: ignore[no-untyped-def]
        if self.check_event(yaml.AliasEvent):  # alias expansion is the YAML bomb vector
            raise yaml.YAMLError("YAML aliases are not supported")
        return super().compose_node(parent, index)


def _load_yaml(content: str) -> dict:
    doc = yaml.load(content, _NoAliasLoader)  # noqa: S506 -- SafeLoader subclass
    if not isinstance(doc, dict):
        raise ValueError("expected a YAML mapping")
    return doc


def _pnpm_package(key: str, direct: bool, entry: dict | None) -> ParsedPackage:
    return ParsedPackage(*_pnpm_split(key), direct, tuple(map(_pnpm_split, _pnpm_children(entry))))


def parse_pnpm_lock(content: str) -> list[ParsedPackage]:
    doc = _load_yaml(content)
    if not str(doc.get("lockfileVersion", "")).startswith(("6", "9")):
        raise ValueError("pnpm-lock.yaml needs lockfileVersion 6 or 9")
    importers = doc.get("importers") or {".": doc}  # v6 without workspaces keeps them top-level
    direct: set[str] = set()
    for imp in importers.values():
        for section in ("dependencies", "optionalDependencies"):  # not devDependencies
            for name, d in (imp.get(section) or {}).items():
                key = _pnpm_key(name, d.get("version", "") if isinstance(d, dict) else d)
                if _registry_version(_pnpm_split(key)[1]):
                    direct.add(key)
    snapshots = doc.get("snapshots")
    if snapshots is None:  # v6: packages carry an explicit `dev` flag
        return [
            _pnpm_package(k, k.lstrip("/") in direct, v)
            for k, v in (doc.get("packages") or {}).items()
            if not v.get("dev") and _registry_version(_pnpm_split(k)[1])
        ]
    prod = _walk(direct, lambda k: _pnpm_children(snapshots.get(k)))
    return [_pnpm_package(k, k in direct, snapshots[k]) for k in prod if k in snapshots]


def _yarn_name(spec: str) -> str:
    """Real package name of a spec: 'x@^1' -> x, 'x@npm:@s/y@^4' -> @s/y (an alias of y)."""
    i = spec.index("@", 1)
    name, rng = spec[:i], spec[i + 1 :]
    return rng[4:].rpartition("@")[0] if rng.startswith("npm:") and "@" in rng[4:] else name


def _yarn_edges(
    deps: dict[str, str], by_spec: dict[str, tuple[str, str]], prefix: str
) -> tuple[tuple[str, str], ...]:
    found = (by_spec.get(f"{n}@{prefix}{r}") or by_spec.get(f"{n}@{r}") for n, r in deps.items())
    return tuple(f for f in found if f)


def parse_yarn_lock(content: str) -> list[ParsedPackage]:
    by_spec: dict[str, tuple[str, str]] = {}
    if "__metadata:" in content:  # berry
        doc = _load_yaml(content)
        entries = [(k, v.get("resolution", ""), v) for k, v in doc.items() if k != "__metadata"]
        for k, res, _ in entries:
            name, _, ver = res.rpartition("@npm:")  # the real name, also for aliases
            if name and _registry_version(ver):
                by_spec.update({s.strip(): (name, ver) for s in k.split(",")})
        direct = {  # resolved through by_spec, so an aliased dependency names the real package
            d
            for _, r, v in entries
            if "@workspace:" in r
            for d in _yarn_edges(v.get("dependencies") or {}, by_spec, "npm:")
        }
        out = []
        for _, res, v in entries:
            name, _, ver = res.rpartition("@npm:")
            if name and _registry_version(ver):
                deps = _yarn_edges(v.get("dependencies") or {}, by_spec, "npm:")
                out.append(ParsedPackage(name, ver, not direct or (name, ver) in direct, deps))
        return out
    parsed = []
    for block in re.split(r"\n\s*\n", content):
        lines = [ln for ln in block.splitlines() if ln and not ln.startswith("#")]
        if not lines or lines[0][0] == " " or not lines[0].endswith(":"):
            continue
        specs = [s.strip().strip('"') for s in lines[0].rstrip(":").split(",")]
        name = _yarn_name(specs[0])
        m = next((m for ln in lines if (m := re.match(r'^  version "?([^"\s]+)"?$', ln))), None)
        if not name or not m or not _registry_version(m[1]):
            continue
        deps: dict[str, str] = {}
        in_deps = False
        for ln in lines[1:]:
            if ln.startswith("    ") and in_deps:
                dn, _, dr = ln.strip().partition(" ")
                deps[dn.strip('"')] = dr.strip('"')
            else:
                in_deps = ln in ("  dependencies:", "  optionalDependencies:")
        by_spec.update({s: (name, m[1]) for s in specs})
        parsed.append((name, m[1], deps))
    # shortcut: yarn v1 does not mark direct or dev deps, so all count as direct prod
    return [ParsedPackage(n, v, True, _yarn_edges(d, by_spec, "")) for n, v, d in parsed]


PARSERS = {
    "pypi": parse_requirements,
    "npm": parse_package_json,
    "cargo": parse_cargo,
    "go": parse_go_mod,
}


LOCKFILES = {
    "uv.lock": ("pypi", parse_uv_lock),
    "poetry.lock": ("pypi", parse_poetry_lock),
    "package-lock.json": ("npm", parse_package_lock),
    "pnpm-lock.yaml": ("npm", parse_pnpm_lock),
    "yarn.lock": ("npm", parse_yarn_lock),
    "cargo.lock": ("cargo", parse_cargo_lock),
}
MANIFEST_ERRORS = (
    ValueError,  # incl. JSONDecodeError
    tomllib.TOMLDecodeError,
    yaml.YAMLError,
    AttributeError,
    TypeError,
    KeyError,
    IndexError,
    RecursionError,
)


def is_lockfile(ecosystem: str, filename: str) -> bool:
    lock = LOCKFILES.get(filename.rsplit("/", 1)[-1].lower())
    return bool(lock and lock[0] == ecosystem)


def parse_manifest(
    ecosystem: str, content: str, filename: str = "", *, pyproject: str | None = None
) -> list[ParsedPackage]:
    """Raises ValueError on malformed input.

    A lockfile (picked by filename) lists exact versions plus transitives, so it is deduped by
    (name, version) and needs no resolution; a manifest is deduped by name (first wins).
    """
    base = filename.rsplit("/", 1)[-1].lower()
    lock = LOCKFILES.get(base)
    lock = lock if lock and lock[0] == ecosystem else None
    if ecosystem not in PARSERS:
        raise ValueError(f"unsupported ecosystem: {ecosystem}")
    if lock:
        parser = lock[1]
    elif base == "pyproject.toml" and ecosystem == "pypi":
        parser = parse_pyproject
    else:
        parser = PARSERS[ecosystem]
    try:
        parsed = parser(content, pyproject) if parser is parse_poetry_lock else parser(content)
    except MANIFEST_ERRORS as e:
        raise ValueError(f"malformed {ecosystem} manifest: {e!r}") from e
    fits = [
        p
        for p in parsed
        if 0 < len(p.name) <= MAX_NAME
        and len(p.version) <= MAX_VERSION
        and "\x00" not in p.name + p.version  # Postgres text cannot hold NUL
    ]
    if len(fits) < len(parsed):
        log.warning("manifest_overlong_dropped", count=len(parsed) - len(fits))
    if len(fits) > MAX_PACKAGES:
        raise ValueError(f"too many packages (limit {MAX_PACKAGES})")
    if lock:
        merged: dict[tuple[str, str], ParsedPackage] = {}
        for p in fits:
            old = merged.get((p.name, p.version))
            merged[(p.name, p.version)] = (
                p
                if old is None
                else ParsedPackage(
                    p.name,
                    p.version,
                    old.is_direct or p.is_direct,
                    tuple(dict.fromkeys(old.deps + p.deps)),
                )
            )
        return list(merged.values())
    return list({p.name: p for p in reversed(fits)}.values())[::-1]


@dataclass(frozen=True)
class PkgRef:
    """A package version already present in the DB."""

    package_id: int
    ecosystem: str
    name: str
    version: str
