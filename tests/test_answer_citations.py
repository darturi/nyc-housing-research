from app.answer.citations import (
    build_public_citations,
    remove_internal_chunk_ids,
    validate_cited_chunk_ids,
)
from app.retrieval.schemas import SearchResult


def test_validate_cited_chunk_ids_allows_only_retrieved_unique_ids():
    retrieved = [
        _result("chunk-1"),
        _result("chunk-2"),
    ]

    cited = validate_cited_chunk_ids(
        ["chunk-2", "unknown", "chunk-2", "chunk-1"],
        retrieved,
    )

    assert cited == ["chunk-2", "chunk-1"]


def test_build_public_citations_uses_retrieved_metadata():
    retrieved = [_result("chunk-1")]

    citations = build_public_citations(["chunk-1"], retrieved)

    assert len(citations) == 1
    assert citations[0].chunk_id == "chunk-1"
    assert citations[0].citation == "NYC Admin Code § 27-2005"
    assert citations[0].source_url == "https://example.test/source"


def test_remove_internal_chunk_ids_preserves_reader_facing_prose():
    answer = (
        "RPAPL applies to summary proceedings. (chunk-1)\n\n"
        "Citations: chunk-1"
    )

    cleaned = remove_internal_chunk_ids(answer, ["chunk-1"])

    assert cleaned == "RPAPL applies to summary proceedings."


def _result(chunk_id: str) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id="document-id",
        source_id="source-id",
        source_version_id="source-version-id",
        source_name="NYC Housing Maintenance Code",
        source_type="law",
        jurisdiction="NYC",
        source_url="https://example.test/source",
        citation="NYC Admin Code § 27-2005",
        title="Duties of owner",
        text="The owner shall keep the premises in good repair.",
        score=1.0,
        match_type="citation",
    )
