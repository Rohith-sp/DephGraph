from typing import Literal

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    service_id: int
    ecosystem: Literal["npm", "pypi", "cargo", "go"]
    content: str = Field(min_length=1, max_length=1_000_000)
    filename: str = Field(min_length=1, max_length=255)


class IngestResult(BaseModel):
    manifest_id: str  # raw_manifests document the Postgres rows were projected from
    deploy_seq: int  # deployment created by this projection
    packages_parsed: int
    packages_inserted: int
    vulns_fetched: int
    duration_ms: int


class ScanResult(BaseModel):
    packages_scanned: int
    vulns_fetched: int
    duration_ms: int
