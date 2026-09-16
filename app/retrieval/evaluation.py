from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from app.retrieval.local import LocalSearch, LocalSearchFilters


@dataclass(frozen=True)
class RetrievalCaseResult:
    case_id: str
    hit: bool
    reciprocal_rank: float
    expected_citations: tuple[str, ...]
    returned_citations: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalEvaluation:
    case_count: int
    recall_at_k: float
    mean_reciprocal_rank: float
    k: int
    passed: bool
    minimum_recall: float
    minimum_mrr: float
    failures: tuple[str, ...]
    cases: tuple[RetrievalCaseResult, ...]


def load_retrieval_cases(path: Path | None = None) -> list[dict]:
    if path is None:
        resource = files("app.resources").joinpath(
            "evaluation/local_retrieval_cases.json"
        )
        payload = json.loads(resource.read_text(encoding="utf-8"))
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) < 50:
        raise ValueError("Retrieval evaluation requires at least 50 labeled cases.")
    for item in payload:
        if (
            not isinstance(item, dict)
            or not item.get("id")
            or not item.get("query")
            or (
                not item.get("expected_citations")
                and item.get("expected_empty") is not True
            )
        ):
            raise ValueError("Retrieval evaluation case schema is invalid.")
    return payload


def evaluate_retrieval(
    search: LocalSearch,
    cases: list[dict],
    *,
    k: int = 5,
    minimum_recall: float = 0.0,
    minimum_mrr: float = 0.0,
) -> RetrievalEvaluation:
    if not 1 <= k <= 20:
        raise ValueError("Evaluation k must be between 1 and 20.")
    results = []
    failures = []
    for item in cases:
        expected = tuple(item["expected_citations"])
        filters = LocalSearchFilters(**item.get("filters", {}))
        response = search.search(item["query"], filters=filters, limit=k)
        returned = tuple(result.citation or "" for result in response.results)
        if item.get("expected_empty") is True:
            rank = 1 if not returned else None
        elif item.get("match_mode") == "all":
            ranks = [
                next(
                    (
                        index
                        for index, citation in enumerate(returned, start=1)
                        if citation == expected_citation
                    ),
                    None,
                )
                for expected_citation in expected
            ]
            rank = max(ranks) if all(value is not None for value in ranks) else None
        else:
            rank = next(
                (
                    index
                    for index, citation in enumerate(returned, start=1)
                    if citation in expected
                ),
                None,
            )
        if rank is None:
            failures.append(str(item["id"]))
        results.append(
            RetrievalCaseResult(
                case_id=str(item["id"]),
                hit=rank is not None,
                reciprocal_rank=0.0 if rank is None else 1.0 / rank,
                expected_citations=expected,
                returned_citations=returned,
            )
        )
    recall = sum(result.hit for result in results) / len(results)
    mrr = sum(result.reciprocal_rank for result in results) / len(results)
    return RetrievalEvaluation(
        case_count=len(results),
        recall_at_k=recall,
        mean_reciprocal_rank=mrr,
        k=k,
        passed=recall >= minimum_recall and mrr >= minimum_mrr,
        minimum_recall=minimum_recall,
        minimum_mrr=minimum_mrr,
        failures=tuple(failures),
        cases=tuple(results),
    )
