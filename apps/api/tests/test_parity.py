"""Ecosystem lists must match the parsers/OSV mapping (single source of truth) everywhere."""

import re
import typing
from pathlib import Path

from app.schemas.ingest import IngestRequest
from app.services.osv_fetcher import OSV_ECOSYSTEM

WEB_SRC = Path(__file__).resolve().parents[2] / "web" / "src" / "lib"


def test_web_ecosystems_match_backend():
    src = (WEB_SRC / "severity.ts").read_text()
    web = set(re.search(r"ECOSYSTEMS = \[(.*?)\]", src).group(1).replace('"', "").split(", "))
    assert web == set(OSV_ECOSYSTEM)


def test_api_schema_enums_match_backend():
    assert set(typing.get_args(IngestRequest.model_fields["ecosystem"].annotation)) == set(
        OSV_ECOSYSTEM
    )
    schema = (WEB_SRC / "api-schema.ts").read_text()
    enums = re.findall(r'ecosystem\??: \(((?:"\w+"(?: \| )?)+)\)', schema)
    assert enums and all(set(re.findall(r"\w+", e)) == set(OSV_ECOSYSTEM) for e in enums)
