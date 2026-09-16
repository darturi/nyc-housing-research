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
        re.compile(
            r"good\s+repair|\brepair\b|\bmaintain(?:ed|ing)?\b|"
            r"\bmaintenance\b|\bowner\b|premises|"
            r"fit\s+for\s+(?:human\s+)?habitation",
            re.IGNORECASE,
        ),
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
    (
        re.compile(
            r"\bnonpayment\b|rent\s+demand|14[-\s]?day\s+demand|"
            r"\bholdover\b|rent\s+acceptance|rent\s+default",
            re.IGNORECASE,
        ),
        (
            "nonpayment", "rent", "demand", "fourteen-day", "holdover",
            "acceptance", "RPAPL", "Real Property Law",
        ),
    ),
    (
        re.compile(
            r"good\s+cause.*\b(defin|terms?)|"
            r"\b(defin|terms?).*good\s+cause",
            re.I,
        ),
        ("RPL", "211", "definitions", "housing accommodation", "landlord", "tenant"),
    ),
    (
        re.compile(
            r"good\s+cause.*\b(covered|coverage|units?)|\bcovered housing",
            re.I,
        ),
        ("RPL", "214", "covered", "housing", "accommodations"),
    ),
    (
        re.compile(
            r"good\s+cause.*\b(notice|notification|disclos|applicability)|"
            r"\b(notice|notification|disclos).*good\s+cause|"
            r"article\s+6-a.*\bnotice",
            re.I,
        ),
        ("RPL", "231-c", "good", "cause", "eviction", "law", "notice"),
    ),
    (
        re.compile(r"\bcertif(?:y|ication)\b|dismiss(?:al|ed)?|eCertification", re.I),
        ("clear", "violations", "certification", "dismissal", "eCertification"),
    ),
)

FOCUSED_QUERY_TEXTS: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (
        re.compile(r"\bheat\b|hot\s+water|temperature", re.IGNORECASE),
        ("temperature", "minimum temperature", "heat"),
    ),
    (
        re.compile(
            r"good\s+repair|\brepair\b|\bmaintain(?:ed|ing)?\b|"
            r"\bmaintenance\b|\bowner\b|premises|"
            r"fit\s+for\s+(?:human\s+)?habitation",
            re.IGNORECASE,
        ),
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
    (
        re.compile(
            r"\bnonpayment\b|rent\s+demand|14[-\s]?day\s+demand|"
            r"\bholdover\b|rent\s+acceptance|rent\s+default",
            re.IGNORECASE,
        ),
        ("RPAPL 711 written demand", "Real Property Law 210 216", "rent demand"),
    ),
    (
        re.compile(
            r"good\s+cause.*\b(defin|terms?)|\b(defin|terms?).*good\s+cause|"
            r"article\s+6-a.*\bdefinitions?",
            re.I,
        ),
        ("RPL 211 definitions", "housing accommodation landlord tenant"),
    ),
    (
        re.compile(
            r"good\s+cause.*\b(covered|coverage|units?)|\bcovered housing",
            re.I,
        ),
        ("RPL 214 covered housing accommodations",),
    ),
    (
        re.compile(
            r"good\s+cause.*\b(notice|notification|disclos|applicability)|"
            r"\b(notice|notification|disclos).*good\s+cause|"
            r"article\s+6-a.*\b(notice|notification)",
            re.I,
        ),
        ("RPL 231-c good cause eviction law notice",),
    ),
    (
        re.compile(r"\bcomplaint\b.*\bfollow|\bfollow.*\bcomplaint|\b311\b", re.I),
        ("report maintenance issue inspection", "complaint status HPD"),
    ),
    (
        re.compile(r"\bcertif(?:y|ication)\b|dismiss(?:al|ed)?|eCertification", re.I),
        ("clear violations", "eCertification", "dismiss violation"),
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
    if any(term in terms for term in ("certification", "ecertification", "dismissal")):
        if any(word in title for word in ("clear violations", "ecertification")):
            score += 6.0
    if any(term in terms for term in ("complaint", "311", "follow")):
        if any(word in title for word in ("report a quality", "report a maintenance")):
            score += 6.0
    if any(term in terms for term in ("nonpayment", "holdover", "demand")):
        if "rpapl" in citation or "real property law" in citation:
            score += 6.0
    return score
