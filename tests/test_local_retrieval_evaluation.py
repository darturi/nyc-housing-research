from types import SimpleNamespace

from app.retrieval.evaluation import evaluate_retrieval, load_retrieval_cases


class FixtureSearch:
    def __init__(self, cases):
        self._cases = {case["query"]: case for case in cases}

    def search(self, query, *, filters, limit):
        del filters, limit
        case = self._cases[query]
        return SimpleNamespace(
            results=tuple(
                SimpleNamespace(citation=citation)
                for citation in case["expected_citations"]
            )
        )


def test_packaged_retrieval_gate_has_fifty_labeled_cases() -> None:
    cases = load_retrieval_cases()
    assert len(cases) >= 50
    assert any(case["category"] == "paraphrase" for case in cases)
    assert any(case["category"] == "multi-source" for case in cases)
    assert any(case["category"] == "incorrect-jurisdiction" for case in cases)
    assert any(case["category"] == "out-of-coverage" for case in cases)
    result = evaluate_retrieval(
        FixtureSearch(cases),
        cases,
        k=5,
        minimum_recall=0.98,
        minimum_mrr=0.98,
    )
    assert result.passed is True
    assert result.recall_at_k == 1.0
    assert result.mean_reciprocal_rank == 1.0
