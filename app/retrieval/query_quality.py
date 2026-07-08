import re

from app.retrieval.schemas import SearchResult

STOP_WORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "can",
    "does",
    "for",
    "how",
    "in",
    "is",
    "of",
    "or",
    "say",
    "the",
    "to",
    "under",
    "what",
    "when",
    "who",
}

DOMAIN_EXPANSIONS: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (
        re.compile(r"\bheat\b|hot\s+water|temperature", re.IGNORECASE),
        (
            "heat",
            "hot",
            "water",
            "temperature",
            "minimum",
            "maintained",
            "season",
        ),
    ),
    (
        re.compile(r"good\s+repair|\brepair\b|\bowner\b|premises", re.IGNORECASE),
        ("owner", "duties", "repair", "good", "premises"),
    ),
    (
        re.compile(r"\bcomplaints?\b|\breport\b|\b311\b", re.IGNORECASE),
        ("complaint", "report", "311", "inspection", "violation"),
    ),
    (
        re.compile(r"\benforcement\b|\bviolations?\b|\binspection\b", re.IGNORECASE),
        ("enforcement", "inspection", "violation", "violations", "correct"),
    ),
)

FOCUSED_QUERY_TEXTS: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (
        re.compile(r"\bheat\b|hot\s+water|temperature", re.IGNORECASE),
        ("temperature", "minimum temperature", "heat"),
    ),
    (
        re.compile(r"good\s+repair|\brepair\b|\bowner\b|premises", re.IGNORECASE),
        ("duties owner", "good repair", "repair premises"),
    ),
    (
        re.compile(r"\bcomplaints?\b|\breport\b|\b311\b", re.IGNORECASE),
        ("complaint", "report complaint", "311 inspection violation"),
    ),
    (
        re.compile(r"\benforcement\b|\bviolations?\b|\binspection\b", re.IGNORECASE),
        ("enforcement", "inspection violation", "owner correction"),
    ),
)


def expand_query_terms(query_text: str) -> list[str]:
    terms = meaningful_terms(query_text)
    for pattern, additions in DOMAIN_EXPANSIONS:
        if pattern.search(query_text):
            terms.extend(additions)
    return dedupe_terms(terms)


def expand_query_text(query_text: str) -> str:
    expanded_terms = expand_query_terms(query_text)
    original_terms = set(meaningful_terms(query_text))
    additions = [term for term in expanded_terms if term not in original_terms]
    if not additions:
        return query_text
    return f"{query_text} {' '.join(additions)}"


def focused_query_texts(query_text: str) -> list[str]:
    queries: list[str] = []
    for pattern, additions in FOCUSED_QUERY_TEXTS:
        if pattern.search(query_text):
            queries.extend(additions)
    return dedupe_terms(queries)


def meaningful_terms(value: str) -> list[str]:
    terms = re.findall(r"[a-z0-9][a-z0-9-]*", value.lower())
    return [term for term in terms if term not in STOP_WORDS and len(term) > 1]


def dedupe_terms(terms: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        output.append(term)
    return output


def rerank_results(
    results: list[SearchResult],
    query_text: str,
) -> list[SearchResult]:
    terms = expand_query_terms(query_text)
    if not terms:
        return results

    lexical_scores = {
        result.chunk_id: lexical_quality_score(result, terms) for result in results
    }
    has_lexical_candidate = any(score > 0 for score in lexical_scores.values())
    for result in results:
        lexical_score = lexical_scores[result.chunk_id]
        if result.match_type == "citation":
            result.score += 100.0
        else:
            result.score += lexical_score
        if (
            has_lexical_candidate
            and result.match_type == "vector"
            and lexical_score == 0
        ):
            result.score -= 5.0
    return sorted(results, key=lambda result: (-result.score, result.chunk_id))


def lexical_quality_score(result: SearchResult, terms: list[str]) -> float:
    title = (result.title or "").lower()
    citation = (result.citation or "").lower()
    text = result.text.lower()
    score = 0.0
    for term in terms:
        if term in citation:
            score += 3.0
        if term in title:
            score += 2.0
        if term in text:
            score += 0.5
    if "owner" in terms and "duties of owner" in title:
        score += 4.0
    if "temperature" in terms and "minimum temperature" in title:
        score += 4.0
    if "complaint" in terms and "complaint" in title:
        score += 3.0
    if "enforcement" in terms and "enforcement" in title:
        score += 3.0
    return score
