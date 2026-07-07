from app.ingestion.citations import (
    detect_citation_type,
    extract_citations,
    normalize_citation,
)


def test_normalize_citation_handles_mvp_families():
    assert normalize_citation("§ 27-2005") == "NYC Admin Code § 27-2005"
    assert (
        normalize_citation("NYC Administrative Code section 27-2005")
        == "NYC Admin Code § 27-2005"
    )
    assert normalize_citation("MDL § 78") == "Multiple Dwelling Law § 78"
    assert (
        normalize_citation("Multiple Dwelling Law section 78")
        == "Multiple Dwelling Law § 78"
    )
    assert normalize_citation("RPAPL 711") == "RPAPL § 711"
    assert normalize_citation("RPAPL section 711") == "RPAPL § 711"


def test_detect_citation_type_handles_mvp_families():
    assert detect_citation_type("§ 27-2005") == "nyc_code"
    assert detect_citation_type("Multiple Dwelling Law § 78") == "ny_law"
    assert detect_citation_type("RPAPL § 711") == "rpapl"


def test_extract_citations_deduplicates_normalized_citations():
    candidates = extract_citations("See § 27-2005 and NYC Admin Code § 27-2005.")

    assert len(candidates) == 1
    assert candidates[0].normalized_citation == "NYC Admin Code § 27-2005"


def test_extract_citations_handles_natural_section_phrasing():
    candidates = extract_citations("What does RPAPL section 711 cover?")

    assert len(candidates) == 1
    assert candidates[0].normalized_citation == "RPAPL § 711"
