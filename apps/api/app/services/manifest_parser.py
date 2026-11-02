"""Parse dependency manifests into (name, version, is_direct) rows."""

import json
import re
import tomllib
from dataclasses import dataclass

import structlog
from packaging.requirements import InvalidRequirement, Requirement

log = structlog.get_logger()


@dataclass(frozen=True)
class ParsedPackage:
    name: str
    version: str
    is_direct: bool = True


def normalize_pypi(name: str) -> str:
    """PEP 503 name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(content: str) -> list[ParsedPackage]:
    out: list[ParsedPackage] = []
    for raw in content.replace("\\\n", " ").splitlines():
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


PARSERS = {
    "pypi": parse_requirements,
    "npm": parse_package_json,
    "cargo": parse_cargo,
    "go": parse_go_mod,
}


def parse_manifest(ecosystem: str, content: str) -> list[ParsedPackage]:
    """Dedup by name (first wins). Raises ValueError on malformed input."""
    try:
        parsed = PARSERS[ecosystem](content)
    except KeyError:
        raise ValueError(f"unsupported ecosystem: {ecosystem}") from None
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, AttributeError) as e:
        raise ValueError(f"malformed {ecosystem} manifest: {e}") from e
    return list({p.name: p for p in reversed(parsed)}.values())[::-1]


@dataclass(frozen=True)
class PkgRef:
    """A package version already present in the DB."""

    package_id: int
    ecosystem: str
    name: str
    version: str
