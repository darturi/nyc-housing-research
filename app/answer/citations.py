from app.answer.schemas import AnswerCitation
from app.retrieval.schemas import SearchResult


def validate_cited_chunk_ids(
    cited_chunk_ids: list[str],
    retrieved_results: list[SearchResult],
) -> list[str]:
    retrieved_ids = {result.chunk_id for result in retrieved_results}
    validated: list[str] = []
    seen: set[str] = set()
    for chunk_id in cited_chunk_ids:
        if chunk_id in retrieved_ids and chunk_id not in seen:
            validated.append(chunk_id)
            seen.add(chunk_id)
    return validated


def build_public_citations(
    cited_chunk_ids: list[str],
    retrieved_results: list[SearchResult],
) -> list[AnswerCitation]:
    results_by_id = {result.chunk_id: result for result in retrieved_results}
    citations: list[AnswerCitation] = []
    for chunk_id in cited_chunk_ids:
        result = results_by_id.get(chunk_id)
        if result is None:
            continue
        citations.append(
            AnswerCitation(
                chunk_id=result.chunk_id,
                citation=result.citation,
                source_name=result.source_name,
                source_url=result.source_url,
            )
        )
    return citations
