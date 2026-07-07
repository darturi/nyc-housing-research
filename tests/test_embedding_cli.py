from app.cli.embeddings import embedding_status, generate_embeddings
from app.db.session import SessionLocal
from app.models.chunk import Chunk
from tests.retrieval_fixtures import create_retrieval_corpus


def test_embedding_generation_is_idempotent_and_updates_changed_text():
    create_retrieval_corpus()

    created, updated, skipped = generate_embeddings()
    assert created == 0
    assert updated == 0
    assert skipped == 2

    with SessionLocal() as db:
        chunk = db.query(Chunk).first()
        chunk.text = f"{chunk.text} Changed."
        chunk.text_hash = "changed-hash"
        db.commit()

    created, updated, skipped = generate_embeddings()

    assert created == 0
    assert updated == 1
    assert skipped == 1


def test_embedding_status_reports_embedded_and_missing_counts():
    create_retrieval_corpus()

    embedded, missing = embedding_status()

    assert embedded == 2
    assert missing == 0
