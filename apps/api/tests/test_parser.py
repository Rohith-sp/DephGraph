from pathlib import Path

import pytest

from app.services.manifest_parser import ParsedPackage, normalize_pypi, parse_manifest

FIX = Path(__file__).parent / "fixtures"


def parse(eco: str, fixture: str) -> dict[str, ParsedPackage]:
    return {p.name: p for p in parse_manifest(eco, (FIX / fixture).read_text())}


def test_requirements_flask():
    pkgs = parse("pypi", "requirements_flask.txt")
    assert len(pkgs) == 8  # -r / --index-url lines ignored, unversioned gunicorn skipped
    assert pkgs["flask"].version == "2.0.1"  # pinned
    assert pkgs["jinja2"].version == "3.0"  # >= lower bound
    assert pkgs["markupsafe"].version == "2.0.1"  # ~=
    assert pkgs["requests"].version == "2.25.1"  # extras + marker
    assert "pyyaml-extra-pkg" in pkgs  # PEP 503
    assert "gunicorn" not in pkgs
    assert all(p.is_direct for p in pkgs.values())


def test_package_json():
    pkgs = parse("npm", "package_react.json")
    assert {n: p.version for n, p in pkgs.items()} == {
        "react": "18.2.0",
        "react-dom": "18.2.0",
        "axios": "1.4.0",
        "lodash": "4.17.0",
        "typescript": "5.1.6",
        "eslint": "8.0.0",
    }  # devDependencies included, "latest" skipped


def test_cargo_toml():
    pkgs = parse("cargo", "Cargo_tokio.toml")
    assert {n: p.version for n, p in pkgs.items()} == {
        "tokio": "1.28",
        "serde": "1.0",
        "serde_json": "1.0.96",
        "criterion": "0.5",
    }  # path dependency skipped


def test_go_mod():
    pkgs = parse("go", "go_gin.mod")
    assert pkgs["github.com/gin-gonic/gin"].is_direct
    assert not pkgs["github.com/bytedance/sonic"].is_direct  # // indirect
    assert pkgs["golang.org/x/net"].version == "v0.10.0"
    assert len(pkgs) == 3


def test_dedup_first_wins():
    out = parse_manifest("pypi", "a==1\nA==2\n")
    assert out == [ParsedPackage("a", "1")]


@pytest.mark.parametrize(
    ("eco", "content"), [("npm", "{not json"), ("cargo", "= broken"), ("maven", "x")]
)
def test_malformed_raises(eco, content):
    with pytest.raises(ValueError):
        parse_manifest(eco, content)


def test_normalize_pypi():
    assert normalize_pypi("Foo_Bar.baz") == "foo-bar-baz"
