"""respx mocks for OSV.dev and deps.dev."""

import json

import httpx
import respx

ADVISORY = {
    "id": "GHSA-test-0001",
    "summary": "Flask test vuln",
    "details": "Long details text",
    "published": "2023-01-01T00:00:00Z",
    "modified": "2023-02-01T00:00:00Z",
    "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
    "affected": [
        {
            "package": {"name": "Flask", "ecosystem": "PyPI"},
            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.2.5"}]}],
        }
    ],
}


def _batch(request: httpx.Request) -> httpx.Response:
    queries = json.loads(request.content)["queries"]
    results = [
        {"vulns": [{"id": ADVISORY["id"]}]} if q["package"]["name"] == "flask" else {}
        for q in queries
    ]
    return httpx.Response(200, json={"results": results})


def mock_upstreams(deps_dev_status: int = 200) -> respx.MockRouter:
    router = respx.mock(assert_all_called=False)
    router.post("https://api.osv.dev/v1/querybatch").mock(side_effect=_batch)
    router.get(f"https://api.osv.dev/v1/vulns/{ADVISORY['id']}").respond(200, json=ADVISORY)
    router.get(url__regex=r"https://api\.deps\.dev/v3/.*").respond(
        deps_dev_status,
        json={
            "licenses": ["GPL-3.0-only"],
            "publishedAt": "2021-05-01T10:00:00Z",
            "isDeprecated": True,
        },
    )
    return router
