import argparse

from sqlalchemy import func, inspect, select, text

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.chunk import Chunk
from app.models.chunk_embedding import ChunkEmbedding
from app.models.source import Source
from app.retrieval.embeddings import get_embedding_provider
from app.retrieval.vector import vector_literal


def chunks_for_generation(db, source_slug: str | None = None):
    query = select(Chunk).order_by(Chunk.id)
    if source_slug:
        query = query.join(Source, Source.id == Chunk.source_id).where(
            Source.slug == source_slug
        )
    return db.scalars(query).all()


def generate_embeddings(source_slug: str | None = None) -> tuple[int, int, int]:
    settings = get_settings()
    provider = get_embedding_provider()
    created = 0
    updated = 0
    skipped = 0
    with SessionLocal() as db:
        has_pgvector_column = pgvector_column_available(db)
        chunks = chunks_for_generation(db, source_slug)
        pending: list[tuple[Chunk, ChunkEmbedding | None]] = []
        for chunk in chunks:
            existing = db.scalar(
                select(ChunkEmbedding).where(
                    ChunkEmbedding.chunk_id == chunk.id,
                    ChunkEmbedding.embedding_model == settings.embedding_model,
                )
            )
            if existing is not None and existing.text_hash == chunk.text_hash:
                if has_pgvector_column:
                    update_embedding_vector(db, existing.id, existing.embedding)
                skipped += 1
                continue
            pending.append((chunk, existing))

        for batch in embedding_batches(
            pending,
            settings.embedding_batch_size,
            settings.embedding_max_input_chars,
        ):
            oversized_single_chunk = (
                len(batch) == 1
                and len(batch[0][0].text) > settings.embedding_max_input_chars
            )
            if oversized_single_chunk:
                embeddings = [provider.embed_text(batch[0][0].text)]
            else:
                embeddings = provider.embed_batch([chunk.text for chunk, _ in batch])
            for (chunk, existing), embedding in zip(batch, embeddings, strict=True):
                was_created = store_embedding(
                    db,
                    chunk,
                    existing,
                    embedding,
                    settings,
                    has_pgvector_column,
                )
                if was_created:
                    created += 1
                else:
                    updated += 1
        db.commit()
    return created, updated, skipped


def embedding_batches(
    pending: list[tuple[Chunk, ChunkEmbedding | None]],
    batch_size: int,
    max_input_chars: int,
) -> list[list[tuple[Chunk, ChunkEmbedding | None]]]:
    batches: list[list[tuple[Chunk, ChunkEmbedding | None]]] = []
    batch: list[tuple[Chunk, ChunkEmbedding | None]] = []
    batch_chars = 0
    max_batch_chars = min(max_input_chars * batch_size, 200000)
    for item in pending:
        chunk_chars = len(item[0].text)
        if chunk_chars > max_input_chars:
            if batch:
                batches.append(batch)
                batch = []
                batch_chars = 0
            batches.append([item])
            continue
        if batch and (
            len(batch) >= batch_size or batch_chars + chunk_chars > max_batch_chars
        ):
            batches.append(batch)
            batch = []
            batch_chars = 0
        batch.append(item)
        batch_chars += chunk_chars
    if batch:
        batches.append(batch)
    return batches


def store_embedding(
    db,
    chunk: Chunk,
    existing: ChunkEmbedding | None,
    embedding: list[float],
    settings,
    has_pgvector_column: bool,
) -> bool:
    if existing is None:
        existing = ChunkEmbedding(
            chunk_id=chunk.id,
            embedding_model=settings.embedding_model,
            embedding_provider=settings.embedding_provider,
            embedding_dimension=settings.embedding_dimension,
            embedding=embedding,
            text_hash=chunk.text_hash,
        )
        db.add(existing)
        db.flush()
        was_created = True
    else:
        existing.embedding_provider = settings.embedding_provider
        existing.embedding_dimension = settings.embedding_dimension
        existing.embedding = embedding
        existing.text_hash = chunk.text_hash
        db.flush()
        was_created = False
    if has_pgvector_column:
        update_embedding_vector(db, existing.id, embedding)
    return was_created


def embedding_status() -> tuple[int, int]:
    settings = get_settings()
    with SessionLocal() as db:
        total_chunks = db.scalar(select(func.count()).select_from(Chunk)) or 0
        embedded_chunks = (
            db.scalar(
                select(func.count())
                .select_from(ChunkEmbedding)
                .where(ChunkEmbedding.embedding_model == settings.embedding_model)
            )
            or 0
        )
    return embedded_chunks, total_chunks - embedded_chunks


def pgvector_column_available(db) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return False
    columns = inspect(db.get_bind()).get_columns("chunk_embeddings")
    return any(column["name"] == "embedding_vector" for column in columns)


def update_embedding_vector(db, embedding_id: str, embedding: list[float]) -> None:
    db.execute(
        text(
            """
            UPDATE chunk_embeddings
            SET embedding_vector = CAST(:embedding AS vector)
            WHERE id = :embedding_id
            """
        ),
        {"embedding": vector_literal(embedding), "embedding_id": embedding_id},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage chunk embeddings.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate")
    generate.add_argument("--source-slug")

    subparsers.add_parser("status")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "generate":
        created, updated, skipped = generate_embeddings(args.source_slug)
        print(f"created: {created}")
        print(f"updated: {updated}")
        print(f"skipped: {skipped}")
    elif args.command == "status":
        embedded, missing = embedding_status()
        print(f"embedded: {embedded}")
        print(f"missing: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
