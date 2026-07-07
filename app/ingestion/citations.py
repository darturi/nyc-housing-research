import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CitationCandidate:
    citation_text: str
    normalized_citation: str
    citation_type: str


SECTION_MARKER = r"(?:§+|sections?|sec\.?)"

HMC_PATTERN = re.compile(
    rf"(?:NYC\s+Admin(?:istrative)?\s+Code|Housing\s+Maintenance\s+Code)?"
    rf"\s*(?:{SECTION_MARKER}\s*)?(27-\d{{3,5}})",
    re.IGNORECASE,
)
MDL_PATTERN = re.compile(
    rf"(?:Multiple\s+Dwelling\s+Law|MDL)\s*(?:{SECTION_MARKER}\s*)?(\d+[a-z]?)",
    re.IGNORECASE,
)
RPAPL_PATTERN = re.compile(
    rf"(?:RPAPL)\s*(?:{SECTION_MARKER}\s*)?(\d+[a-z]?)",
    re.IGNORECASE,
)


def normalize_citation(citation_text: str) -> str:
    compact = " ".join(citation_text.strip().split())
    if match := HMC_PATTERN.search(compact):
        return f"NYC Admin Code § {match.group(1).upper()}"
    if match := MDL_PATTERN.search(compact):
        return f"Multiple Dwelling Law § {match.group(1).upper()}"
    if match := RPAPL_PATTERN.search(compact):
        return f"RPAPL § {match.group(1).upper()}"
    return compact


def detect_citation_type(citation_text: str) -> str:
    if HMC_PATTERN.search(citation_text):
        return "nyc_code"
    if MDL_PATTERN.search(citation_text):
        return "ny_law"
    if RPAPL_PATTERN.search(citation_text):
        return "rpapl"
    return "guidance"


def extract_citations(text: str) -> list[CitationCandidate]:
    candidates: list[CitationCandidate] = []
    seen: set[str] = set()
    for pattern in (HMC_PATTERN, MDL_PATTERN, RPAPL_PATTERN):
        for match in pattern.finditer(text):
            original = match.group(0)
            normalized = normalize_citation(original)
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(
                CitationCandidate(
                    citation_text=original,
                    normalized_citation=normalized,
                    citation_type=detect_citation_type(original),
                )
            )
    return candidates
