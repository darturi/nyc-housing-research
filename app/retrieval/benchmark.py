from __future__ import annotations

import hashlib
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sqlalchemy import insert, update

from app.retrieval.local import LocalSearch
from app.storage.database import LocalStorage
from app.storage.schema import (
    chunks,
    corpus_state,
    documents,
    embedding_profiles,
    embeddings,
    generation_chunks,
    generation_sources,
    generations,
    source_modules,
    source_versions,
)
from app.workspace.paths import resolve_workspace_paths

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no resource module
    resource = None


@dataclass(frozen=True)
class RetrievalBenchmark:
    chunk_count: int
    vector_dimension: int
    runs: int
    startup_seconds: float
    keyword_cold_seconds: float
    keyword_p95_seconds: float
    hybrid_cold_seconds: float
    hybrid_p95_seconds: float
    database_bytes: int
    process_peak_rss_bytes: int | None
    targets: dict[str, float]
    passes: dict[str, bool]
    conditions: str


def run_synthetic_benchmark(
    *, chunk_count: int = 10_000, dimension: int = 1_536, runs: int = 20
) -> RetrievalBenchmark:
    if not 100 <= chunk_count <= 100_000:
        raise ValueError("Benchmark chunk count must be between 100 and 100,000.")
    if not 16 <= dimension <= 4_096:
        raise ValueError("Benchmark dimension must be between 16 and 4,096.")
    if not 5 <= runs <= 100:
        raise ValueError("Benchmark runs must be between 5 and 100.")
    profile_id = f"benchmark-{dimension}"
    with tempfile.TemporaryDirectory(prefix="nyc-housing-benchmark-") as directory:
        paths = resolve_workspace_paths(
            Path(directory) / "workspace", environment={}
        )
        storage = LocalStorage.open(paths, initialize=True)
        try:
            _populate(storage, chunk_count, dimension, profile_id)
        finally:
            storage.close()

        started = time.perf_counter()
        storage = LocalStorage.open(paths)
        storage.versions()
        startup_seconds = time.perf_counter() - started
        try:
            search = LocalSearch(storage)
            started = time.perf_counter()
            search.search("repair heat tenant", limit=10)
            keyword_cold = time.perf_counter() - started
            keyword_times = _measure(
                runs, lambda: search.search("repair heat tenant", limit=10)
            )
            query_vector = [0.0] * dimension
            query_vector[0] = 1.0
            started = time.perf_counter()
            search.search(
                "repair heat tenant",
                limit=10,
                query_vector=query_vector,
                embedding_profile_id=profile_id,
            )
            hybrid_cold = time.perf_counter() - started
            hybrid_times = _measure(
                runs,
                lambda: search.search(
                    "repair heat tenant",
                    limit=10,
                    query_vector=query_vector,
                    embedding_profile_id=profile_id,
                ),
            )
            database_bytes = paths.corpus_database.stat().st_size
            rss = _peak_rss_bytes()
        finally:
            storage.close()
    targets = {
        "startup_seconds": 5.0,
        "keyword_p95_seconds": 0.5,
        "hybrid_p95_seconds": 1.0,
        "idle_rss_bytes": float(500 * 1024**2),
    }
    keyword_p95 = _percentile95(keyword_times)
    hybrid_p95 = _percentile95(hybrid_times)
    return RetrievalBenchmark(
        chunk_count=chunk_count,
        vector_dimension=dimension,
        runs=runs,
        startup_seconds=startup_seconds,
        keyword_cold_seconds=keyword_cold,
        keyword_p95_seconds=keyword_p95,
        hybrid_cold_seconds=hybrid_cold,
        hybrid_p95_seconds=hybrid_p95,
        database_bytes=database_bytes,
        process_peak_rss_bytes=rss,
        targets=targets,
        passes={
            "startup": startup_seconds < targets["startup_seconds"],
            "keyword_p95": keyword_p95 < targets["keyword_p95_seconds"],
            "hybrid_p95": hybrid_p95 < targets["hybrid_p95_seconds"],
            # Peak RSS includes fixture construction and is recorded for context;
            # a clean serve-process measurement is the release idle-RSS gate.
            "peak_rss_context_only": True,
        },
        conditions=(
            "Synthetic generation-scoped SQLite/FTS5 corpus; normalized float32 "
            "vectors; remote embedding latency excluded; warm p95 after one cold run."
        ),
    )


def benchmark_as_dict(result: RetrievalBenchmark) -> dict:
    return asdict(result)


def _populate(
    storage: LocalStorage,
    chunk_count: int,
    dimension: int,
    profile_id: str,
) -> None:
    now = datetime.now(UTC)
    artifact = storage.paths.artifacts / "benchmark-source.txt"
    artifact.write_text("Synthetic benchmark source; not legal authority.\n")
    content_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    basis = np.eye(dimension, dtype="<f4")
    encoded = [basis[index].tobytes() for index in range(dimension)]
    checksums = [hashlib.sha256(value).hexdigest() for value in encoded]
    with storage.corpus_engine.begin() as connection:
        connection.execute(
            insert(source_modules).values(
                id="benchmark-source",
                slug="benchmark-source",
                name="Synthetic benchmark source",
                source_type="fixture",
                publisher="Local benchmark",
                jurisdiction="fixture",
                source_url="https://example.invalid/benchmark",
                scope_json='{"synthetic":true}',
                manifest_json='{"synthetic":true}',
                enabled=True,
            )
        )
        connection.execute(
            insert(source_versions).values(
                id="benchmark-version",
                source_module_id="benchmark-source",
                content_hash=content_hash,
                parser_version="benchmark-1",
                artifact_uri=artifact.relative_to(storage.paths.root).as_posix(),
                retrieved_at=now,
                last_checked_at=now,
                validation_state="synthetic",
                validation_json='{"synthetic":true}',
            )
        )
        connection.execute(
            insert(documents).values(
                id="benchmark-document",
                source_version_id="benchmark-version",
                stable_id="benchmark-document",
                title="Synthetic benchmark document",
                source_url="https://example.invalid/benchmark",
            )
        )
        connection.execute(
            insert(embedding_profiles).values(
                id=profile_id,
                provider="fixture",
                model="basis-vector",
                dimension=dimension,
                preprocessing_json='{"synthetic":true}',
                created_at=now,
            )
        )
        connection.execute(
            insert(generations).values(
                id="benchmark-generation",
                status="active",
                profile_id=profile_id,
                readiness="hybrid_ready",
                is_partial=True,
                validation_json='{"synthetic":true}',
                created_at=now,
                activated_at=now,
            )
        )
        connection.execute(
            insert(generation_sources).values(
                generation_id="benchmark-generation",
                source_version_id="benchmark-version",
            )
        )
        for start in range(0, chunk_count, 250):
            stop = min(chunk_count, start + 250)
            chunk_rows = []
            generation_rows = []
            embedding_rows = []
            fts_rows = []
            for index in range(start, stop):
                chunk_id = f"benchmark-{index:06d}"
                body = (
                    "Synthetic housing benchmark evidence about repair heat tenant "
                    f"topic-{index:06d}."
                )
                chunk_rows.append(
                    {
                        "id": chunk_id,
                        "document_id": "benchmark-document",
                        "source_module_id": "benchmark-source",
                        "source_version_id": "benchmark-version",
                        "stable_id": chunk_id,
                        "citation": f"BENCH § {index}",
                        "title": f"Benchmark {index}",
                        "text": body,
                        "text_hash": hashlib.sha256(body.encode()).hexdigest(),
                    }
                )
                generation_rows.append(
                    {
                        "generation_id": "benchmark-generation",
                        "chunk_id": chunk_id,
                        "text_ready": True,
                        "embedding_ready": True,
                    }
                )
                vector_index = index % dimension
                embedding_rows.append(
                    {
                        "id": hashlib.sha256(chunk_id.encode()).hexdigest(),
                        "chunk_id": chunk_id,
                        "profile_id": profile_id,
                        "dimension": dimension,
                        "dtype": "float32-le",
                        "normalized": True,
                        "vector_bytes": encoded[vector_index],
                        "checksum": checksums[vector_index],
                        "created_at": now,
                    }
                )
                fts_rows.append(
                    {
                        "chunk_id": chunk_id,
                        "generation_id": "benchmark-generation",
                        "title": f"Benchmark {index}",
                        "citation": f"BENCH § {index}",
                        "body": body,
                    }
                )
            connection.execute(insert(chunks), chunk_rows)
            connection.execute(insert(generation_chunks), generation_rows)
            connection.execute(insert(embeddings), embedding_rows)
            connection.exec_driver_sql(
                "INSERT INTO chunk_fts "
                "(chunk_id, generation_id, title, citation, body) "
                "VALUES (:chunk_id, :generation_id, :title, :citation, :body)",
                fts_rows,
            )
        connection.execute(
            update(corpus_state)
            .where(corpus_state.c.id == 1)
            .values(active_generation_id="benchmark-generation")
        )


def _measure(runs: int, operation) -> list[float]:
    measured = []
    for _index in range(runs):
        started = time.perf_counter()
        operation()
        measured.append(time.perf_counter() - started)
    return measured


def _percentile95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _peak_rss_bytes() -> int | None:
    if resource is None:
        return None
    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (AttributeError, ValueError):
        return None
    # macOS reports bytes; Linux and the BSDs generally report KiB.
    return int(peak if os.sys.platform == "darwin" else peak * 1024)
