from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, insert, select, update

from app.corpus.embeddings import EmbeddingProfile, LocalEmbeddingStore
from app.corpus.service import CorpusService, CorpusValidationError
from app.jobs.runtime import CancellationSignal, Deadline
from app.providers.gateway import ProviderGateway
from app.providers.profiles import ProfileKind, ProviderProfile
from app.storage.database import LocalStorage
from app.storage.schema import (
    chunks,
    corpus_state,
    embeddings,
    generation_chunks,
    generation_sources,
    generations,
)


@dataclass(frozen=True)
class IndexingResult:
    generation_id: str
    profile_id: str
    embedded_chunks: int
    reused_chunks: int


@dataclass(frozen=True)
class IndexingEstimate:
    profile_id: str
    total_chunks: int
    chunks_requiring_embedding: int
    reusable_chunks: int
    estimated_input_tokens: int
    estimated_cost_usd: Decimal


class CorpusEmbeddingIndexer:
    def __init__(self, storage: LocalStorage, gateway: ProviderGateway) -> None:
        self._storage = storage
        self._gateway = gateway

    def estimate_active(self, profile: ProviderProfile) -> IndexingEstimate:
        if profile.kind != ProfileKind.EMBEDDING or profile.dimension is None:
            raise CorpusValidationError("A compatible embedding profile is required.")
        with self._storage.corpus_engine.connect() as connection:
            active = connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
            if active is None:
                raise CorpusValidationError("No active legal corpus is installed.")
            chunk_rows = list(
                connection.execute(
                    select(chunks.c.id, chunks.c.text)
                    .select_from(
                        generation_chunks.join(
                            chunks, chunks.c.id == generation_chunks.c.chunk_id
                        )
                    )
                    .where(generation_chunks.c.generation_id == active)
                ).mappings()
            )
            reusable = set(
                connection.scalars(
                    select(embeddings.c.chunk_id).where(
                        embeddings.c.profile_id == profile.id,
                        embeddings.c.chunk_id.in_([row["id"] for row in chunk_rows]),
                    )
                )
            )
        missing = [row for row in chunk_rows if row["id"] not in reusable]
        tokens = sum(
            max(1, (len(row["text"].encode("utf-8")) + 3) // 4) for row in missing
        )
        if profile.input_usd_per_million is None:
            raise CorpusValidationError(
                "The selected embedding profile has no verified price."
            )
        estimated_cost = (
            Decimal(tokens) * profile.input_usd_per_million / Decimal(1_000_000)
        ).quantize(Decimal("0.00000001"))
        return IndexingEstimate(
            profile_id=profile.id,
            total_chunks=len(chunk_rows),
            chunks_requiring_embedding=len(missing),
            reusable_chunks=len(reusable),
            estimated_input_tokens=tokens,
            estimated_cost_usd=estimated_cost,
        )

    def index_active(
        self,
        profile: ProviderProfile,
        *,
        credential: str | None,
        approve_cost: bool,
        operation_id: str | None = None,
        batch_size: int = 32,
        cancellation: CancellationSignal | None = None,
        deadline: Deadline | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> IndexingResult:
        if profile.kind != ProfileKind.EMBEDDING or profile.dimension is None:
            raise CorpusValidationError("A compatible embedding profile is required.")
        if profile.paid and not approve_cost:
            raise CorpusValidationError(
                "Paid corpus indexing requires explicit cost approval."
            )
        if not 1 <= batch_size <= 128:
            raise ValueError("Embedding batch size must be between 1 and 128.")
        staged, missing, reused = self._stage(profile)
        operation_id = operation_id or str(uuid.uuid4())
        store = LocalEmbeddingStore(self._storage)
        embedding_profile = EmbeddingProfile(
            id=profile.id,
            provider=profile.provider,
            model=profile.model,
            dimension=profile.dimension,
            preprocessing={"normalization": "l2", "schema_version": 1},
        )
        completed = 0
        for offset in range(0, len(missing), batch_size):
            if cancellation:
                cancellation.raise_if_cancelled()
            if deadline:
                deadline.raise_if_expired()
            batch = missing[offset : offset + batch_size]
            response = self._gateway.embeddings(
                inputs=[row["text"] for row in batch],
                profile=profile,
                credential=credential,
                operation_id=operation_id,
                deadline=deadline,
                cancellation=cancellation,
            )
            for row, vector in zip(batch, response.vectors, strict=True):
                store.put(row["id"], embedding_profile, list(vector))
            with self._storage.corpus_engine.begin() as connection:
                connection.execute(
                    update(generation_chunks)
                    .where(
                        generation_chunks.c.generation_id == staged,
                        generation_chunks.c.chunk_id.in_([row["id"] for row in batch]),
                    )
                    .values(embedding_ready=True)
                )
            completed += len(batch)
            if progress:
                progress(completed, len(missing))
        if cancellation:
            cancellation.raise_if_cancelled()
        if deadline:
            deadline.raise_if_expired()
        with self._storage.corpus_engine.begin() as connection:
            total = connection.scalar(
                select(func.count())
                .select_from(generation_chunks)
                .where(generation_chunks.c.generation_id == staged)
            )
            ready = connection.scalar(
                select(func.count())
                .select_from(generation_chunks)
                .where(
                    generation_chunks.c.generation_id == staged,
                    generation_chunks.c.embedding_ready.is_(True),
                )
            )
            if total != ready:
                raise CorpusValidationError("Embedding coverage is incomplete.")
            connection.execute(
                update(generations)
                .where(generations.c.id == staged)
                .values(readiness="hybrid_ready")
            )
            CorpusService(self._storage)._activate_in_transaction(
                connection, staged, datetime.now(UTC)
            )
        return IndexingResult(staged, profile.id, completed, reused)

    def _stage(self, profile: ProviderProfile):
        staged = str(uuid.uuid4())
        with self._storage.corpus_engine.begin() as connection:
            active = connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
            if active is None:
                raise CorpusValidationError("No active legal corpus is installed.")
            active_row = (
                connection.execute(
                    select(generations).where(generations.c.id == active)
                )
                .mappings()
                .one()
            )
            source_ids = list(
                connection.scalars(
                    select(generation_sources.c.source_version_id).where(
                        generation_sources.c.generation_id == active
                    )
                )
            )
            chunk_rows = [
                dict(row)
                for row in connection.execute(
                    select(chunks.c.id, chunks.c.text)
                    .select_from(
                        generation_chunks.join(
                            chunks, chunks.c.id == generation_chunks.c.chunk_id
                        )
                    )
                    .where(generation_chunks.c.generation_id == active)
                    .order_by(chunks.c.id)
                ).mappings()
            ]
            ready_ids = set(
                connection.scalars(
                    select(embeddings.c.chunk_id).where(
                        embeddings.c.profile_id == profile.id,
                        embeddings.c.chunk_id.in_([row["id"] for row in chunk_rows]),
                    )
                )
            )
            connection.execute(
                insert(generations).values(
                    id=staged,
                    status="staged",
                    profile_id=profile.id,
                    readiness="embedding_staging",
                    is_partial=active_row["is_partial"],
                    validation_json=json.dumps(
                        {"derived_from": active, "embedding_profile": profile.id},
                        sort_keys=True,
                    ),
                    created_at=datetime.now(UTC),
                    activated_at=None,
                )
            )
            if source_ids:
                connection.execute(
                    insert(generation_sources),
                    [
                        {"generation_id": staged, "source_version_id": source_id}
                        for source_id in source_ids
                    ],
                )
            if chunk_rows:
                connection.execute(
                    insert(generation_chunks),
                    [
                        {
                            "generation_id": staged,
                            "chunk_id": row["id"],
                            "text_ready": True,
                            "embedding_ready": row["id"] in ready_ids,
                        }
                        for row in chunk_rows
                    ],
                )
            CorpusService(self._storage)._build_fts(connection, staged)
        missing = [row for row in chunk_rows if row["id"] not in ready_ids]
        return staged, missing, len(ready_ids)
