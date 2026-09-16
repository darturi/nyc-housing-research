from app.retrieval.review_cases import load_legal_review_cases


def test_packaged_legal_review_fixture_preserves_all_twenty_eight_cases() -> None:
    payload = load_legal_review_cases()
    cases = payload["cases"]

    assert payload["review_status"] == "domain_review_required"
    assert len(cases) == 28
    assert {case["route"] for case in cases} == {"legal", "property"}
    assert any(case["expected_behavior"] == "unsupported" for case in cases)
    assert any("required_missing_facts" in case for case in cases)
    assert next(case for case in cases if case["id"] == "legal-25")[
        "relevant_citations"
    ] == [
        "Real Property Law § 211",
        "Real Property Law § 212",
        "Real Property Law § 214",
    ]
