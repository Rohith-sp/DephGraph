from pathlib import Path

from sqlalchemy import text

from app.database import SessionLocal, get_mongo
from app.services import blast_radius
from app.services.queries import latest_seq
from tests.upstream import mock_upstreams

FIX = Path(__file__).parent / "fixtures"


def uv_body(service_id: int, werkzeug: str) -> dict:
    content = (FIX / "uv.lock").read_text().replace("3.0.1", werkzeug)
    return {
        "service_id": service_id,
        "ecosystem": "pypi",
        "content": content,
        "filename": "uv.lock",
    }


async def resolved(service_id: int) -> set[str]:
    async with SessionLocal() as s:
        found = await s.execute(
            text(
                "SELECT p.name || '@' || rt.version_string FROM resolves_to rt "
                "JOIN package p USING (package_id) WHERE rt.service_id = :s AND rt.deploy_seq = "
                "(SELECT max(deploy_seq) FROM deployment WHERE service_id = :s)"
            ),
            {"s": service_id},
        )
        return {r[0] for r in found}


async def test_lockfile_deployment_is_not_shaped_by_other_projects_edges(client, auth, reseed):
    with mock_upstreams():
        first = await client.post("/ingest/manifest", json=uv_body(1, "3.0.1"), headers=auth)
        r = await client.post("/ingest/manifest", json=uv_body(2, "3.0.2"), headers=auth)
    assert first.status_code == 200 and r.status_code == 200, r.text
    assert await resolved(2) == {"flask@3.0.0", "werkzeug@3.0.2"}  # no phantom werkzeug@3.0.1
    assert await resolved(1) == {"flask@3.0.0", "werkzeug@3.0.1"}
    async with SessionLocal() as s, s.begin():
        _, edges = await blast_radius.load_closure(s, 2, await latest_seq(s, 2))
    # only service 2's own resolution; service 1's werkzeug 3.0.1 edge is outside its set
    assert {e["ref_version_string"] for e in edges} == {"3.0.2"}


async def test_lockfile_upgrade_drops_the_old_version(client, auth, reseed):
    with mock_upstreams():
        await client.post("/ingest/manifest", json=uv_body(1, "3.0.1"), headers=auth)
        await client.post("/ingest/manifest", json=uv_body(1, "3.0.2"), headers=auth)
    assert await resolved(1) == {"flask@3.0.0", "werkzeug@3.0.2"}


async def count(sql: str, **p) -> int:
    async with SessionLocal() as s:
        return (await s.execute(text(sql), p)).scalar_one()


async def test_lockfile_edges_live_with_the_deployment_not_in_depends_on(client, auth, reseed):
    before = await count("SELECT count(*) FROM depends_on")
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json=uv_body(1, "3.0.1"), headers=auth)
    seq = r.json()["data"]["deploy_seq"]
    assert await count("SELECT count(*) FROM depends_on") == before
    assert await count("SELECT locked FROM deployment WHERE service_id=1 AND deploy_seq=:q", q=seq)
    assert await count("SELECT count(*) FROM deployment_edge WHERE service_id=1") == 1
    async with SessionLocal() as s, s.begin():  # the lockfile's path is found: flask -> werkzeug
        nodes, edges = await blast_radius.load_closure(s, 1, seq)
        paths = blast_radius.shortest_paths(nodes, edges)
    assert ["flask@3.0.0", "werkzeug@3.0.1"] in paths.values()
    await count_delete(seq)
    assert await count("SELECT count(*) FROM deployment_edge WHERE service_id=1") == 0  # cascade


async def count_delete(seq: int) -> None:
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text("DELETE FROM deployment WHERE service_id=1 AND deploy_seq=:q"), {"q": seq}
        )


async def test_plain_manifest_is_unaffected_by_another_projects_lockfile(client, auth, reseed):
    plain = {
        "service_id": 2,
        "ecosystem": "pypi",
        "content": "flask==3.0.0\nwerkzeug==3.0.1\n",
        "filename": "requirements.txt",
    }
    with mock_upstreams():
        await client.post("/ingest/manifest", json=uv_body(1, "3.0.1"), headers=auth)
        r = await client.post("/ingest/manifest", json=plain, headers=auth)
    assert r.status_code == 200, r.text
    async with SessionLocal() as s, s.begin():
        _, edges = await blast_radius.load_closure(s, 2, r.json()["data"]["deploy_seq"])
    assert edges == []  # service 1's lockfile edge flask -> werkzeug is not service 2's business


async def test_raw_document_stores_no_edges(client, auth, reseed):
    with mock_upstreams():
        await client.post("/ingest/manifest", json=uv_body(1, "3.0.1"), headers=auth)
    doc = await get_mongo()["raw_manifests"].find_one({"service_id": 1, "filename": "uv.lock"})
    assert all(set(p) == {"name", "version", "is_direct"} for p in doc["parsed_packages"])
