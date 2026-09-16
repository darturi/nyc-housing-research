from dataclasses import replace
from decimal import Decimal

import pytest

from app.answer.evaluation import (
    _automated_case_check,
    estimate_answer_evaluation,
    run_answer_evaluation,
)
from app.storage.database import LocalStorage
from app.usage.ledger import SpendDenied
from app.workspace.context import WorkspaceContext


def _paid_workspace(tmp_path):
    context = WorkspaceContext.from_options(
        tmp_path / "evaluation workspace",
        environment={},
        initialize=True,
    )
    context = replace(
        context,
        settings=replace(
            context.settings,
            answer_profile="openai-answer-luna-v1",
            embedding_profile="openai-embedding-3-small-v1",
        ),
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    return context, storage


def test_paid_answer_evaluation_requires_approval_before_provider_work(
    tmp_path,
) -> None:
    context, storage = _paid_workspace(tmp_path)
    try:
        estimate = estimate_answer_evaluation(context)
        assert estimate.legal_case_count == 26
        assert estimate.property_case_count == 2
        assert estimate.conservative_max_cost_usd > 0
        assert estimate.review_status == "domain_review_required"
        with pytest.raises(ValueError, match="explicit cost approval"):
            run_answer_evaluation(
                context,
                storage,
                approve_cost=False,
                max_cost_usd=estimate.conservative_max_cost_usd,
            )
    finally:
        storage.close()


def test_paid_answer_evaluation_refuses_a_ceiling_below_estimate(tmp_path) -> None:
    context, storage = _paid_workspace(tmp_path)
    try:
        with pytest.raises(SpendDenied, match="exceeds the approved ceiling"):
            run_answer_evaluation(
                context,
                storage,
                approve_cost=True,
                max_cost_usd=Decimal("0"),
            )
    finally:
        storage.close()


def test_answer_evaluation_checks_required_source_as_well_as_citation() -> None:
    assert _automated_case_check(
        "general_answer",
        "answered",
        (),
        (),
        "hpd-guidance",
        ("hpd-guidance",),
    )
    assert not _automated_case_check(
        "general_answer",
        "answered",
        (),
        (),
        "hpd-guidance",
        ("ny-rpapl",),
    )
