"""Semantic clustering: SBERT -> UMAP 2D -> HDBSCAN -> c-TF-IDF labels (PRD §9 phase 5)."""

import asyncio
import json
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
import structlog
from pymongo import UpdateOne
from sklearn.feature_extraction.text import CountVectorizer
from sqlalchemy import text

from app.database import SessionLocal, get_mongo
from app.services.queries import rows

log = structlog.get_logger()

MODEL_NAME = "all-MiniLM-L6-v2"
MIN_CLUSTER_SIZE = 5
LOCK_KEY = "dephgraph:lock:cluster_recompute"
LOCK_TTL = 900

_model: Any = None
_model_lock = threading.Lock()


def load_model() -> Any:
    """Lazy singleton; the ~80MB download happens once per process."""
    global _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(MODEL_NAME)
    return _model


def encode(texts: list[str]) -> np.ndarray:
    """Tests monkeypatch this with a deterministic fake."""
    return np.asarray(load_model().encode(texts, batch_size=64), dtype=np.float32)


@dataclass
class Clustering:
    coords: np.ndarray  # (n, 2)
    labels: np.ndarray  # (n,) cluster index, -1 = noise


def cluster(vectors: np.ndarray) -> Clustering:
    import hdbscan
    import umap

    n = len(vectors)
    if n < MIN_CLUSTER_SIZE:
        return Clustering(np.zeros((n, 2)), np.full(n, -1))
    coords = umap.UMAP(
        n_components=2,
        n_neighbors=max(2, min(15, n - 1)),
        random_state=42,
        init=("random" if n < 20 else "spectral"),  # spectral init needs n >> n_components
    ).fit_transform(vectors)
    labels = hdbscan.HDBSCAN(min_cluster_size=MIN_CLUSTER_SIZE).fit_predict(coords)
    return Clustering(coords, labels)


def top_keywords(docs: list[str], labels: np.ndarray, k: int = 3) -> dict[int, list[str]]:
    """c-TF-IDF: one document per cluster, term score = tf * log(1 + avg_len / term_freq)."""
    ids = sorted(set(labels.tolist()) - {-1})
    if not ids:
        return {}
    class_docs = [" ".join(d for d, lab in zip(docs, labels) if lab == i) for i in ids]
    vec = CountVectorizer(stop_words="english", token_pattern=r"[A-Za-z][A-Za-z]{2,}")
    counts = vec.fit_transform(class_docs).toarray().astype(float)
    tf = counts / np.maximum(counts.sum(axis=1, keepdims=True), 1)
    idf = np.log(1 + counts.sum() / len(ids) / np.maximum(counts.sum(axis=0), 1))
    terms = vec.get_feature_names_out()
    return {i: [terms[j] for j in np.argsort(-(tf[r] * idf))[:k]] for r, i in enumerate(ids)}


def _label(words: list[str]) -> str:
    return " / ".join(words)[:120]


async def recompute() -> int:
    """Replace all clusters atomically in Postgres, then upsert embeddings in Mongo."""
    async with SessionLocal() as session:
        pkgs = await rows(
            session,
            "SELECT package_id, name, description FROM package ORDER BY package_id",
        )
    docs = [f"{p['name']}: {p['description'] or ''}" for p in pkgs]
    vectors = await asyncio.to_thread(encode, docs) if docs else np.zeros((0, 384))
    result = await asyncio.to_thread(cluster, vectors)
    words = top_keywords(docs, result.labels)

    cluster_of: dict[int, int] = {}  # package_id -> new cluster_id
    async with SessionLocal() as session, session.begin():
        await session.execute(text("DELETE FROM semantic_cluster"))  # FK SET NULLs package_version
        for idx, kw in words.items():
            members = np.flatnonzero(result.labels == idx)
            centroid = vectors[members].mean(axis=0).tolist()
            cid = (
                await session.execute(
                    text(
                        "INSERT INTO semantic_cluster (label, centroid_vec, member_count, keywords) "
                        "VALUES (:label, CAST(:centroid AS JSONB), :n, :kw) RETURNING cluster_id"
                    ),
                    {
                        "label": _label(kw),
                        "centroid": json.dumps(centroid),
                        "n": len(members),
                        "kw": kw,
                    },
                )
            ).scalar_one()
            ids = [pkgs[m]["package_id"] for m in members]
            cluster_of.update(dict.fromkeys(ids, cid))
            await session.execute(
                text("UPDATE package_version SET cluster_id = :c WHERE package_id = ANY(:ids)"),
                {"c": cid, "ids": ids},
            )

    if pkgs:
        await get_mongo()["package_embeddings"].bulk_write(
            [
                UpdateOne(
                    {"package_id": p["package_id"]},
                    {
                        "$set": {
                            "embedding": vectors[i].tolist(),
                            "umap_2d": [float(x) for x in result.coords[i]],
                            "cluster_id": cluster_of.get(p["package_id"]),
                        }
                    },
                    upsert=True,
                )
                for i, p in enumerate(pkgs)
            ]
        )
    log.info("cluster_recompute_done", packages=len(pkgs), clusters=len(words))
    return len(words)


async def run_guarded(redis: Any) -> None:
    """Background entry: lock already held by the caller; always logs, always releases."""
    try:
        await recompute()
    except Exception:  # noqa: BLE001 -- background job: log and swallow so the lock is released
        log.exception("cluster_recompute_failed")
    finally:
        await redis.delete(LOCK_KEY)
