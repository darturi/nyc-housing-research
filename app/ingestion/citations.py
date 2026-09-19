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
    rf"\s*(?:{SECTION_MARKER}\s*)?((?:27-\d{{3,5}}|26-5\d{{2}}(?:\.\d+)?))",
    re.IGNORECASE,
)
MDL_PATTERN = re.compile(
    rf"(?:Multiple\s+Dwelling\s+Law|MDL)\s*(?:{SECTION_MARKER}\s*)?"
    r"(\d+(?:-[a-z]+|[a-z])?)",
    re.IGNORECASE,
)
RPAPL_PATTERN = re.compile(
    rf"(?:RPAPL)\s*(?:{SECTION_MARKER}\s*)?(\d+(?:-[a-z]+|[a-z])?)",
    re.IGNORECASE,
)
RPL_PATTERN = re.compile(
    rf"(?:Real\s+Property\s+Law|RPL)\s*(?:{SECTION_MARKER}\s*)?"
    r"(21[0-6]|231-c)",
    re.IGNORECASE,
)

NUMBER_WORD_VALUES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
NUMBER_WORD = "(?:" + "|".join(NUMBER_WORD_VALUES) + "|hundred)"
REVERSED_SPOKEN_CITATION_PATTERN = re.compile(
    rf"\b(?:section|sec\.?)\s+(?P<number>{NUMBER_WORD}(?:[-\s]+{NUMBER_WORD})*)"
    r"\s+(?P<law>MDL|multiple\s+dwelling\s+law|RPL|real\s+property\s+law)\b",
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
    if match := RPL_PATTERN.search(compact):
        return f"Real Property Law § {match.group(1).upper()}"
    if spoken := _spoken_citation(compact):
        return spoken
    return compact


def _spoken_citation(value: str) -> str | None:
    # This common spoken citation omits "hundred" but is unambiguous in the
    # supported housing-law vocabulary.
    if re.search(r"\bsection\s+seven[-\s]+eleven\b", value, re.IGNORECASE):
        return "RPAPL § 711"
    match = REVERSED_SPOKEN_CITATION_PATTERN.search(value)
    if not match:
        return None
    number = _number_words_to_int(match.group("number"))
    law = match.group("law").lower()
    prefix = (
        "Multiple Dwelling Law"
        if law in {"mdl", "multiple dwelling law"}
        else "Real Property Law"
    )
    return f"{prefix} § {number}"


def _number_words_to_int(value: str) -> int:
    total = 0
    current = 0
    for word in re.split(r"[-\s]+", value.lower()):
        if word == "hundred":
            current = max(current, 1) * 100
        else:
            current += NUMBER_WORD_VALUES[word]
    total += current
    return total


def detect_citation_type(citation_text: str) -> str:
    if HMC_PATTERN.search(citation_text):
        return "nyc_code"
    if MDL_PATTERN.search(citation_text):
        return "ny_law"
    if RPAPL_PATTERN.search(citation_text):
        return "rpapl"
    if RPL_PATTERN.search(citation_text):
        return "ny_law"
    return "guidance"


def extract_citations(text: str) -> list[CitationCandidate]:
    candidates: list[CitationCandidate] = []
    seen: set[str] = set()
    for pattern in (HMC_PATTERN, MDL_PATTERN, RPAPL_PATTERN, RPL_PATTERN):
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
