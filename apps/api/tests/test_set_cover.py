from app.services.set_cover import greedy_set_cover


def test_greedy_set_cover_spec():
    # Known optimal: pkg_A covers {v1,v2}, pkg_B covers {v3}
    result = greedy_set_cover(
        open_vulns={1, 2, 3},
        pkg_coverage={"pkg_A": {1, 2}, "pkg_B": {3}, "pkg_C": {1}},
    )
    assert result[0]["package"] == "pkg_A"
    assert result[-1]["cumulative_coverage_pct"] == 100.0
    assert len(result) == 2  # optimal


def test_uncoverable_vulns_stay_open():
    result = greedy_set_cover({1, 2, 3, 4}, {"a": {1, 2}, "b": {2}})
    assert [r["package"] for r in result] == ["a"]
    assert result[-1]["cumulative_coverage_pct"] == 50.0


def test_empty():
    assert greedy_set_cover(set(), {"a": {1}}) == []
    assert greedy_set_cover({1}, {}) == []
