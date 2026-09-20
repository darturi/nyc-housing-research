from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path

from app.answer.local import LocalAnswerService
from app.credentials.store import CredentialResolver
from app.jobs.runtime import Deadline
from app.providers.gateway import ProviderGateway
from app.providers.profiles import (
    ProfileKind,
    ProviderProfile,
    get_configured_profile,
)
from app.providers.tokens import input_token_bound
from app.retrieval.review_cases import load_legal_review_cases
from app.storage.database import LocalStorage
from app.usage.ledger import SpendDenied, UsageLedger
from app.workspace.context import WorkspaceContext


@dataclass(frozen=True)
class AnswerEvaluationEstimate:
    legal_case_count: int
    property_case_count: int
    answer_profile_id: str
    embedding_profile_id: str
    conservative_max_cost_usd: Decimal
    review_status: str


@dataclass(frozen=True)
class AnswerEvaluationCase:
    case_id: str
    question: str
    expected_behavior: str
    required_propositions: tuple[str, ...]
    required_missing_facts: tuple[str, ...]
    expected_source: str | None
    answer_status: str
    returned_source_slugs: tuple[str, ...]
    returned_citations: tuple[str, ...]
    expected_citations: tuple[str, ...]
    automated_check_passed: bool
    answer: str
    error: str | None


@dataclass(frozen=True)
class AnswerEvaluationReport:
    evaluation_id: str
    execution_status: str
    automated_checks_passed: bool
    review_status: str
    answer_profile_id: str
    embedding_profile_id: str
    case_count: int
    skipped_property_cases: int
    estimated_ceiling_usd: Decimal
    settled_cost_usd: Decimal
    uncertain_cost_usd: Decimal
    cases: tuple[AnswerEvaluationCase, ...]


def estimate_answer_evaluation(
    context: WorkspaceContext,
    *,
    case_file: Path | None = None,
) -> AnswerEvaluationEstimate:
    payload = load_legal_review_cases(case_file)
    legal_cases = [item for item in payload["cases"] if item["route"] == "legal"]
    property_cases = [item for item in payload["cases"] if item["route"] == "property"]
    answer = get_configured_profile(context.settings, ProfileKind.ANSWER)
    embedding = get_configured_profile(context.settings, ProfileKind.EMBEDDING)
    answer_cost = _maximum_answer_cost(answer) * len(legal_cases)
    embedding_cost = sum(
        _embedding_question_cost(embedding, item["question"]) for item in legal_cases
    )
    return AnswerEvaluationEstimate(
        legal_case_count=len(legal_cases),
        property_case_count=len(property_cases),
        answer_profile_id=answer.id,
        embedding_profile_id=embedding.id,
        conservative_max_cost_usd=(answer_cost + embedding_cost).quantize(
            Decimal("0.00000001")
        ),
        review_status=str(payload["review_status"]),
    )


def run_answer_evaluation(
    context: WorkspaceContext,
    storage: LocalStorage,
    *,
    approve_cost: bool,
    max_cost_usd: Decimal | None,
    case_file: Path | None = None,
    deadline_per_case_seconds: float = 60,
) -> AnswerEvaluationReport:
    estimate = estimate_answer_evaluation(context, case_file=case_file)
    answer_profile = get_configured_profile(context.settings, ProfileKind.ANSWER)
    embedding_profile = get_configured_profile(context.settings, ProfileKind.EMBEDDING)
    if (
        not answer_profile.compatibility_verified
        or not embedding_profile.compatibility_verified
    ):
        raise ValueError(
            "Every custom provider endpoint must pass its explicit compatibility "
            "check before answer evaluation."
        )
    paid = answer_profile.paid or embedding_profile.paid
    if paid and not approve_cost:
        raise ValueError("Paid answer evaluation requires explicit cost approval.")
    if paid and max_cost_usd is None:
        raise ValueError("Paid answer evaluation requires an explicit cost ceiling.")
    effective_ceiling = max_cost_usd or Decimal("0")
    if not effective_ceiling.is_finite() or effective_ceiling < 0:
        raise ValueError("Answer evaluation cost ceiling must be nonnegative.")
    if estimate.conservative_max_cost_usd > effective_ceiling:
        raise SpendDenied(
            "The conservative answer-evaluation estimate exceeds the approved ceiling."
        )
    configured_cap = Decimal(context.settings.per_operation_budget_usd)
    hard_cap = min(configured_cap, effective_ceiling) if paid else configured_cap
    if paid and estimate.conservative_max_cost_usd > hard_cap:
        raise SpendDenied(
            "The workspace per-operation budget is below the evaluation estimate."
        )
    budget_before = UsageLedger(storage).summary(
        monthly_cap_usd=Decimal(context.settings.monthly_budget_usd),
        timezone=context.settings.budget_timezone,
    )
    if paid and estimate.conservative_max_cost_usd > budget_before.remaining_usd:
        raise SpendDenied(
            "The remaining local monthly budget is below the evaluation estimate."
        )
    bounded_context = replace(
        context,
        settings=replace(
            context.settings,
            per_operation_budget_usd=str(hard_cap),
        ),
    )
    payload = load_legal_review_cases(case_file)
    cases = [item for item in payload["cases"] if item["route"] == "legal"]
    evaluation_id = str(uuid.uuid4())
    before = budget_before
    gateway = ProviderGateway(
        bounded_context, UsageLedger(storage), operation_cap_usd=hard_cap
    )
    results: list[AnswerEvaluationCase] = []
    execution_status = "complete"
    try:
        service = LocalAnswerService(
            bounded_context,
            storage,
            gateway,
            CredentialResolver(bounded_context),
        )
        for item in cases:
            result = service.answer(
                item["question"],
                deadline=Deadline.after(deadline_per_case_seconds),
                operation_id=evaluation_id,
            )
            returned = tuple(
                evidence.citation or evidence.title or ""
                for evidence in result.evidence
            )
            returned_sources = tuple(
                dict.fromkeys(evidence.source_slug for evidence in result.evidence)
            )
            expected = tuple(item.get("relevant_citations", []))
            expected_source = (
                str(item["required_source"]) if item.get("required_source") else None
            )
            automated_pass = _automated_case_check(
                str(item["expected_behavior"]),
                result.status,
                expected,
                returned,
                expected_source,
                returned_sources,
            )
            results.append(
                AnswerEvaluationCase(
                    case_id=str(item["id"]),
                    question=str(item["question"]),
                    expected_behavior=str(item["expected_behavior"]),
                    required_propositions=tuple(item["required_propositions"]),
                    required_missing_facts=tuple(
                        item.get("required_missing_facts", [])
                    ),
                    expected_source=expected_source,
                    answer_status=result.status,
                    returned_source_slugs=returned_sources,
                    returned_citations=returned,
                    expected_citations=expected,
                    automated_check_passed=automated_pass,
                    answer=result.answer,
                    error=result.error,
                )
            )
            if result.status == "provider_error":
                execution_status = "stopped_provider_error"
                break
    finally:
        gateway.close()
    after = UsageLedger(storage).summary(
        monthly_cap_usd=Decimal(context.settings.monthly_budget_usd),
        timezone=context.settings.budget_timezone,
    )
    automated_checks_passed = len(results) == len(cases) and all(
        result.automated_check_passed for result in results
    )
    return AnswerEvaluationReport(
        evaluation_id=evaluation_id,
        execution_status=execution_status,
        automated_checks_passed=automated_checks_passed,
        review_status="domain_review_required",
        answer_profile_id=answer_profile.id,
        embedding_profile_id=embedding_profile.id,
        case_count=len(results),
        skipped_property_cases=estimate.property_case_count,
        estimated_ceiling_usd=estimate.conservative_max_cost_usd,
        settled_cost_usd=max(Decimal("0"), after.settled_usd - before.settled_usd),
        uncertain_cost_usd=max(
            Decimal("0"), after.uncertain_usd - before.uncertain_usd
        ),
        cases=tuple(results),
    )


def answer_evaluation_payload(value: object) -> dict:
    payload = asdict(value)
    for key in (
        "conservative_max_cost_usd",
        "estimated_ceiling_usd",
        "settled_cost_usd",
        "uncertain_cost_usd",
    ):
        if key in payload:
            payload[key] = str(payload[key])
    return payload


def _maximum_answer_cost(profile: ProviderProfile) -> Decimal:
    if profile.input_usd_per_million is None:
        raise ValueError("Answer evaluation profile input price is unknown.")
    if profile.output_usd_per_million is None:
        raise ValueError("Answer evaluation profile output price is unknown.")
    output_tokens = profile.max_output_tokens or 0
    return (
        Decimal(profile.max_input_tokens) * profile.input_usd_per_million
        + Decimal(output_tokens) * profile.output_usd_per_million
    ) / Decimal(1_000_000)


def _embedding_question_cost(profile: ProviderProfile, question: str) -> Decimal:
    if profile.input_usd_per_million is None:
        raise ValueError("Embedding evaluation profile price is unknown.")
    tokens = input_token_bound(question)
    return Decimal(tokens) * profile.input_usd_per_million / Decimal(1_000_000)


def _automated_case_check(
    expected_behavior: str,
    answer_status: str,
    expected_citations: tuple[str, ...],
    returned_citations: tuple[str, ...],
    expected_source: str | None,
    returned_sources: tuple[str, ...],
) -> bool:
    if expected_behavior in {"unsupported", "unsupported_outcome_prediction"}:
        return answer_status in {"unsupported", "insufficient_coverage"}
    if answer_status not in {"answered", "synthetic_demo"}:
        return False
    citations_pass = not expected_citations or bool(
        set(expected_citations) & set(returned_citations)
    )
    source_pass = expected_source is None or expected_source in returned_sources
    return citations_pass and source_pass
