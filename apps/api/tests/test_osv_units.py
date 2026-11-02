from app.services.deps_dev_client import is_copyleft
from app.services.manifest_parser import PkgRef
from app.services.osv_fetcher import classify, fixed_in

V3_CRIT = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"


def test_classify_cvss_vector():
    assert classify({"severity": [{"type": "CVSS_V3", "score": V3_CRIT}]}) == ("CRITICAL", 9.8)


def test_classify_fallback_and_unknown():
    assert classify({"database_specific": {"severity": "MODERATE"}}) == ("MEDIUM", None)
    assert classify({"severity": [{"type": "CVSS_V3", "score": "garbage"}]}) == (None, None)


def test_fixed_in_picks_lowest_fix_above_version():
    osv = {
        "affected": [
            {
                "package": {"name": "Django", "ecosystem": "PyPI"},
                "ranges": [
                    {"events": [{"introduced": "0"}, {"fixed": "3.2.10"}]},
                    {"events": [{"introduced": "4.0"}, {"fixed": "4.0.2"}]},
                ],
            }
        ]
    }
    ref = PkgRef(1, "pypi", "django", "3.2.1")
    assert fixed_in(osv, ref) == "3.2.10"
    assert fixed_in(osv, PkgRef(1, "pypi", "django", "9.0")) is None
    assert fixed_in(osv, PkgRef(1, "pypi", "other", "3.2.1")) is None


def test_is_copyleft():
    assert is_copyleft("GPL-3.0-only")
    assert not is_copyleft("MIT")
    assert not is_copyleft("MIT OR GPL-2.0")
    assert is_copyleft("MIT AND GPL-2.0")
