import re

from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.retrieval.citations import citation_lookup
from app.retrieval.keyword import keyword_search
from app.retrieval.query_quality import (
    expand_query_text,
    focused_query_texts,
    rerank_results,
)
from app.retrieval.schemas import SearchFilters, SearchResult
from app.retrieval.vector import vector_search

SOURCE_HINTS = (
    (
        re.compile(
            r"\bHPD\b.*\b(complaint|inspection|certif|dismiss|clear|"
            r"ecertification)\b|\b(complaint|ecertification|clear\s+violations)\b",
            re.I,
        ),
        "hpd-guidance",
        "HPD Tenant and Owner Guidance",
    ),
    (
        re.compile(
            r"\bgood\s+cause\b|rent\s+acceptance|"
            r"\bRPL\s*(?:§|section)?\s*21[0-6]",
            re.I,
        ),
        "ny-real-property-law-good-cause",
        "New York Real Property Law Article 6-A (Good Cause Eviction)",
    ),
    (
        re.compile(
            r"\bnonpayment\b|rent\s+demand|14[-\s]?day\s+demand|\bholdover\b",
            re.I,
        ),
        "ny-rpapl",
        "New York Real Property Actions and Proceedings Law",
    ),
    (
        re.compile(r"\bRPAPL\b|real\s+property\s+actions\s+and\s+proceedings", re.I),
        "ny-rpapl",
        "New York Real Property Actions and Proceedings Law",
    ),
    (
        re.compile(r"\bMDL\b|multiple\s+dwelling\s+law", re.I),
        "ny-multiple-dwelling-law",
        "New York Multiple Dwelling Law",
    ),
    (
        re.compile(
            r"\bHMC\b|housing\s+maintenance\s+code|"
            r"(?:NYC\s+)?admin(?:istrative)?\s+code|\b27-\d{3,5}\b",
            re.I,
        ),
        "nyc-housing-maintenance-code",
        "NYC Housing Maintenance Code",
    ),
)


def merge_results(result_sets: list[list[SearchResult]]) -> list[SearchResult]:
    settings = get_settings()
    weights = {
        "citation": settings.search_citation_weight,
        "keyword": settings.search_keyword_weight,
        "vector": settings.search_vector_weight,
    }
    merged: dict[str, SearchResult] = {}
    score_totals: dict[str, float] = {}
    match_types: dict[str, set[str]] = {}
    for results in result_sets:
        for result in results:
            weighted_score = result.score * weights.get(result.match_type, 1.0)
            if result.chunk_id not in merged:
                merged[result.chunk_id] = result
                score_totals[result.chunk_id] = 0.0
                match_types[result.chunk_id] = set()
            score_totals[result.chunk_id] += weighted_score
            match_types[result.chunk_id].add(result.match_type)

    output: list[SearchResult] = []
    for chunk_id, result in merged.items():
        result.score = score_totals[chunk_id]
        if "citation" in match_types[chunk_id]:
            result.match_type = "citation"
            result.score += 10.0
        elif len(match_types[chunk_id]) > 1:
            result.match_type = "hybrid"
        else:
            result.match_type = next(iter(match_types[chunk_id]))
        output.append(result)
    return sorted(output, key=lambda result: (-result.score, result.chunk_id))


def hybrid_search(
    db: DbSession,
    query_text: str,
    filters: SearchFilters,
    limit: int,
) -> list[SearchResult]:
    expanded_query_text = expand_query_text(query_text)
    focused_queries = focused_query_texts(query_text)
    candidates = [
        citation_lookup(db, query_text, filters, limit),
        keyword_search(db, query_text, filters, limit),
        vector_search(db, query_text, filters, limit),
    ]
    if expanded_query_text != query_text:
        candidates.append(keyword_search(db, expanded_query_text, filters, limit))
    for focused_query_text in focused_queries:
        candidates.append(keyword_search(db, focused_query_text, filters, limit))
    hinted_filters = source_hint_filters(query_text, filters)
    if hinted_filters is not None:
        candidates.extend(
            [
                citation_lookup(db, query_text, hinted_filters, limit),
                keyword_search(db, query_text, hinted_filters, limit),
                vector_search(db, query_text, hinted_filters, limit),
            ]
        )
        if expanded_query_text != query_text:
            candidates.append(
                keyword_search(db, expanded_query_text, hinted_filters, limit)
            )
        for focused_query_text in focused_queries:
            candidates.append(
                keyword_search(db, focused_query_text, hinted_filters, limit)
            )
    boosted = apply_source_hint_boost(merge_results(candidates), query_text)
    return rerank_results(boosted, query_text)[:limit]


def source_hint_for_query(query_text: str) -> tuple[str, str] | None:
    for pattern, source_slug, source_name in SOURCE_HINTS:
        if pattern.search(query_text):
            return source_slug, source_name
    return None


def source_hint_filters(
    query_text: str,
    filters: SearchFilters,
) -> SearchFilters | None:
    if filters.source_slug or filters.source_id or filters.document_id:
        return None
    hint = source_hint_for_query(query_text)
    if hint is None:
        return None
    source_slug, _ = hint
    return SearchFilters(
        source_type=filters.source_type,
        jurisdiction=filters.jurisdiction,
        source_slug=source_slug,
        source_id=filters.source_id,
        document_id=filters.document_id,
    )


def apply_source_hint_boost(
    results: list[SearchResult],
    query_text: str,
) -> list[SearchResult]:
    hint = source_hint_for_query(query_text)
    if hint is None:
        return results
    _, source_name = hint
    for result in results:
        if result.source_name == source_name:
            result.score += 2.0
    return sorted(results, key=lambda result: (-result.score, result.chunk_id))
