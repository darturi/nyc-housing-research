from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import insert, select, update

from app.storage.database import LocalStorage
from app.storage.schema import embedding_profiles, embeddings


class EmbeddingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class EmbeddingProfile:
    id: str
    provider: str
    model: str
    dimension: int
    preprocessing: dict[str, object]


class LocalEmbeddingStore:
    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage

    def ensure_profile(self, profile: EmbeddingProfile) -> None:
        if profile.dimension <= 0:
            raise EmbeddingValidationError("Embedding dimension must be positive.")
        with self._storage.corpus_engine.begin() as connection:
            row = (
                connection.execute(
                    select(embedding_profiles).where(
                        embedding_profiles.c.id == profile.id
                    )
                )
                .mappings()
                .one_or_none()
            )
            values = {
                "provider": profile.provider,
                "model": profile.model,
                "dimension": profile.dimension,
                "preprocessing_json": json.dumps(profile.preprocessing, sort_keys=True),
            }
            if row is None:
                connection.execute(
                    insert(embedding_profiles).values(
                        id=profile.id,
                        created_at=datetime.now(UTC),
                        **values,
                    )
                )
            elif any(row[key] != value for key, value in values.items()):
                raise EmbeddingValidationError(
                    f"Embedding profile ID {profile.id!r} has different metadata."
                )

    def put(
        self,
        chunk_id: str,
        profile: EmbeddingProfile,
        vector: list[float],
    ) -> None:
        self.ensure_profile(profile)
        normalized = _normalize(vector, expected_dimension=profile.dimension)
        vector_bytes = struct.pack(f"<{profile.dimension}f", *normalized)
        checksum = hashlib.sha256(vector_bytes).hexdigest()
        embedding_id = hashlib.sha256(f"{chunk_id}:{profile.id}".encode()).hexdigest()
        with self._storage.corpus_engine.begin() as connection:
            existing = connection.scalar(
                select(embeddings.c.id).where(
                    embeddings.c.chunk_id == chunk_id,
                    embeddings.c.profile_id == profile.id,
                )
            )
            if existing is None:
                connection.execute(
                    insert(embeddings).values(
                        id=embedding_id,
                        chunk_id=chunk_id,
                        profile_id=profile.id,
                        dimension=profile.dimension,
                        dtype="float32-le",
                        normalized=True,
                        vector_bytes=vector_bytes,
                        checksum=checksum,
                        created_at=datetime.now(UTC),
                    )
                )
            else:
                connection.execute(
                    update(embeddings)
                    .where(embeddings.c.id == existing)
                    .values(
                        dimension=profile.dimension,
                        dtype="float32-le",
                        normalized=True,
                        vector_bytes=vector_bytes,
                        checksum=checksum,
                    )
                )


def decode_vector(
    vector_bytes: bytes,
    *,
    dimension: int,
    dtype: str,
    checksum: str,
) -> tuple[float, ...]:
    if dtype != "float32-le":
        raise EmbeddingValidationError(f"Unsupported vector dtype: {dtype}")
    if hashlib.sha256(vector_bytes).hexdigest() != checksum:
        raise EmbeddingValidationError("Embedding checksum mismatch.")
    if len(vector_bytes) != dimension * 4:
        raise EmbeddingValidationError(
            "Embedding byte length does not match dimension."
        )
    values = struct.unpack(f"<{dimension}f", vector_bytes)
    if any(not math.isfinite(value) for value in values):
        raise EmbeddingValidationError("Embedding contains non-finite values.")
    return values


def normalize_query_vector(vector: list[float], dimension: int) -> tuple[float, ...]:
    return _normalize(vector, expected_dimension=dimension)


def _normalize(vector: list[float], *, expected_dimension: int) -> tuple[float, ...]:
    if len(vector) != expected_dimension:
        raise EmbeddingValidationError(
            f"Expected dimension {expected_dimension}, received {len(vector)}."
        )
    if any(not math.isfinite(value) for value in vector):
        raise EmbeddingValidationError("Embedding contains non-finite values.")
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        raise EmbeddingValidationError("Embedding vector cannot be all zeroes.")
    return tuple(value / magnitude for value in vector)
