from pathlib import Path

from app.services.manifest_parser import parse_manifest

FIX = Path(__file__).parent / "fixtures"


def lock(eco: str, fixture: str, filename: str) -> dict[tuple[str, str], bool]:
    out = parse_manifest(eco, (FIX / fixture).read_text(), filename)
    return {(p.name, p.version): p.is_direct for p in out}


def edges(eco: str, fixture: str, filename: str) -> dict[str, set[tuple[str, str]]]:
    out = parse_manifest(eco, (FIX / fixture).read_text(), filename)
    return {p.name: set(p.deps) for p in out if p.deps}


def test_uv_follows_extras_into_optional_dependencies():
    got = lock("pypi", "uv_extras.lock", "uv.lock")
    assert got == {("httpx", "0.27.0"): True, ("h2", "4.1.0"): False}  # click: extra not asked for
    assert edges("pypi", "uv_extras.lock", "uv.lock") == {"httpx": {("h2", "4.1.0")}}


def test_poetry_lock_20_without_marks_needs_pyproject():
    text = (FIX / "poetry_20.lock").read_text()
    alone = parse_manifest("pypi", text, "poetry.lock")  # dev unknowable: all kept (warned)
    assert {p.name for p in alone} == {"django", "asgiref", "black"}
    pyproject = '[tool.poetry.dependencies]\npython = "^3.11"\ndjango = "^4"\n'
    out = {p.name: p for p in parse_manifest("pypi", text, "poetry.lock", pyproject=pyproject)}
    assert set(out) == {"django", "asgiref"}  # black is not reachable from main deps
    assert out["django"].is_direct and not out["asgiref"].is_direct
    assert out["django"].deps == (("asgiref", "3.7.0"),)


def test_package_lock_workspaces_and_non_registry_installs():
    got = lock("npm", "package-lock-workspaces.json", "package-lock.json")
    assert got == {("express", "4.18.2"): True}  # member's dep is direct; git/file/link skipped


def test_pnpm_v6_skips_links_and_walks_optional_dependencies():
    got = lock("npm", "pnpm-lock-v6-link.yaml", "pnpm-lock.yaml")
    assert got == {
        ("react", "18.2.0"): True,
        ("loose-envify", "1.4.0"): False,
        ("fsevents", "2.3.3"): False,
    }
    assert edges("npm", "pnpm-lock-v6-link.yaml", "pnpm-lock.yaml") == {
        "react": {("loose-envify", "1.4.0"), ("fsevents", "2.3.3")}
    }


def test_pnpm_v9_aliases_and_optional_dependencies():
    got = lock("npm", "pnpm-lock-v9-alias.yaml", "pnpm-lock.yaml")
    assert got == {
        ("strip-ansi", "7.1.0"): True,  # importer alias `strip` resolves to the real package
        ("ansi-regex", "6.0.1"): False,  # reached through a child alias
        ("react", "18.2.0"): True,
        ("fsevents", "2.3.3"): False,  # optionalDependencies are walked
    }
    assert edges("npm", "pnpm-lock-v9-alias.yaml", "pnpm-lock.yaml")["strip-ansi"] == {
        ("ansi-regex", "6.0.1")
    }


def test_yarn_v1_alias_header_names_the_real_package():
    got = lock("npm", "yarn_v1_alias.lock", "yarn.lock")
    assert ("string-width", "4.2.3") in got and ("string-width-cjs", "4.2.3") not in got
    assert edges("npm", "yarn_v1_alias.lock", "yarn.lock")["cliui"] == {("string-width", "4.2.3")}


def test_yarn_berry_alias_edges_match_ids():
    assert edges("npm", "yarn_berry_alias.lock", "yarn.lock") == {
        "string-width": {("emoji-regex", "8.0.0")}
    }
    assert lock("npm", "yarn_berry_alias.lock", "yarn.lock")[("string-width", "4.2.3")] is True


def test_empty_package_name_is_dropped():
    doc = '{"packages": {"node_modules/": {"version": "1.0.0"}}}'
    assert parse_manifest("npm", doc, "package-lock.json") == []
