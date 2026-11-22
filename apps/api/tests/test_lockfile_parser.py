from pathlib import Path

import pytest

from app.services.manifest_parser import ParsedPackage, parse_manifest

FIX = Path(__file__).parent / "fixtures"


def lock(eco: str, fixture: str, filename: str) -> dict[tuple[str, str], bool]:
    out = parse_manifest(eco, (FIX / fixture).read_text(), filename)
    return {(p.name, p.version): p.is_direct for p in out}


def test_pyproject_pep621_and_poetry():
    text = (FIX / "pyproject_poetry.toml").read_text()
    got = {p.name: p.version for p in parse_manifest("pypi", text, "pyproject.toml")}
    # extras, dev group, python and path deps are excluded
    assert got == {"flask": "2.0", "requests": "2.31.0", "django": "4.2.1", "celery": "5.3"}


def test_uv_lock_excludes_dev():
    assert lock("pypi", "uv.lock", "uv.lock") == {
        ("flask", "3.0.0"): True,
        ("werkzeug", "3.0.1"): False,
    }


def test_poetry_lock_excludes_dev_and_non_registry():
    got = set(lock("pypi", "poetry.lock", "poetry.lock"))
    assert got == {("django", "4.2.1"), ("asgiref", "3.7.0")}


def test_cargo_lock():
    assert lock("cargo", "Cargo.lock", "sub/Cargo.lock") == {
        ("serde", "1.0.160"): True,
        ("tokio", "1.28.0"): True,
        ("pin-project-lite", "0.2.9"): False,
    }


def test_package_lock_v3():
    assert lock("npm", "package-lock.json", "package-lock.json") == {
        ("express", "4.18.2"): True,
        ("accepts", "1.3.8"): False,  # hoisted + nested copy collapse to one row
        ("qs", "6.11.0"): False,
    }


def test_pnpm_v9_walks_snapshots_without_dev():
    assert lock("npm", "pnpm-lock-v9.yaml", "pnpm-lock.yaml") == {
        ("react", "18.2.0"): True,
        ("loose-envify", "1.4.0"): False,
    }


def test_pnpm_v6_uses_dev_flag():
    assert lock("npm", "pnpm-lock-v6.yaml", "pnpm-lock.yaml") == {
        ("react", "18.2.0"): True,
        ("@scope/util", "2.1.0"): False,
    }


def test_yarn_v1_and_berry():
    got = set(lock("npm", "yarn_v1.lock", "yarn.lock"))
    assert got == {("@babel/core", "7.2.0"), ("lodash", "4.17.21")}
    assert lock("npm", "yarn_berry.lock", "yarn.lock") == {
        ("lodash", "4.17.21"): True,
        ("@babel/helper", "7.2.0"): False,
    }


def test_lockfile_name_must_match_ecosystem():
    # a pypi upload named like an npm lockfile falls back to the requirements parser
    out = parse_manifest("pypi", "flask==2.0.1\n", "package-lock.json")
    assert out == [ParsedPackage("flask", "2.0.1")]


@pytest.mark.parametrize(
    ("eco", "filename", "content"),
    [
        ("pypi", "pyproject.toml", "= broken"),
        ("pypi", "uv.lock", "[[package]\nname"),
        ("pypi", "poetry.lock", "[[package]]\nversion = '1'"),  # no name
        ("cargo", "Cargo.lock", "[[package]]\nname = 'x'\nsource = 'registry+u'"),  # no version
        ("npm", "package-lock.json", "{not json"),
        ("npm", "package-lock.json", '{"lockfileVersion": 1, "dependencies": {}}'),
        ("npm", "package-lock.json", '{"packages": {"": [], "node_modules/a": 5}}'),
        ("npm", "pnpm-lock.yaml", "a: [unclosed"),
        ("npm", "pnpm-lock.yaml", "- just\n- a list"),
        ("npm", "pnpm-lock.yaml", "lockfileVersion: '5.4'"),
        ("npm", "pnpm-lock.yaml", "lockfileVersion: '9.0'\nx: &a [1]\ny: *a"),  # aliases refused
        ("npm", "yarn.lock", "__metadata:\n  version: [oops"),
    ],
)
def test_malformed_lockfile_raises_value_error(eco, filename, content):
    with pytest.raises(ValueError):
        parse_manifest(eco, content, filename)


def test_package_cap(monkeypatch):
    monkeypatch.setattr("app.services.manifest_parser.MAX_PACKAGES", 1)
    with pytest.raises(ValueError, match="too many"):
        parse_manifest("pypi", "a==1\nb==2\n")
