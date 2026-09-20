import json
import os
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from app.cli.main import main
from app.credentials.store import (
    CredentialResolver,
    CredentialStoreError,
    SecretFileCredentialStore,
)
from app.providers.gateway import ProviderExecutionError, ProviderGateway
from app.providers.profiles import ProfileKind, get_configured_profile, get_profile
from app.providers.validation import (
    credential_validation_estimate,
    profile_compatibility_estimate,
    run_credential_validation,
    run_profile_compatibility_check,
)
from app.storage.database import LocalStorage
from app.usage.ledger import UsageLedger
from app.workspace.context import WorkspaceContext
from app.workspace.settings import save_local_settings


def test_secret_file_is_explicit_owner_only_and_redacted(tmp_path) -> None:
    path = tmp_path / "credentials.json"
    store = SecretFileCredentialStore(path)
    store.set("openai", "sk-example-credential")

    assert store.get("openai") == "sk-example-credential"
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
    assert "credentials" not in json.loads(json.dumps({"settings": "ordinary"}))


def test_environment_takes_precedence_without_persisting(tmp_path) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    resolver = CredentialResolver(
        context, environment={"NYC_HOUSING_OPENAI_API_KEY": "sk-from-environment"}
    )
    resolver.secret_file.set("openai", "sk-from-file-value")

    value, source = resolver.resolve("openai")
    assert value == "sk-from-environment"
    assert source == "environment"


def test_broad_secret_file_permissions_are_rejected(tmp_path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX permissions are not applicable on Windows")
    path = tmp_path / "credentials.json"
    path.write_text('{"openai":"sk-example-credential"}', encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(CredentialStoreError, match="permissions"):
        SecretFileCredentialStore(path).get("openai")


def test_cli_hidden_credential_and_profile_selection(
    tmp_path, monkeypatch, capsys
) -> None:
    root = tmp_path / "workspace"
    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    capsys.readouterr()
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "sk-hidden-example-key")

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "credentials",
                "set",
                "openai",
                "--storage",
                "file",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "present": True,
        "provider": "openai",
        "source": "secret_file",
    }
    assert "sk-hidden" not in json.dumps(result)

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "profiles",
                "select",
                "answer",
                "openai-answer-luna-v1",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["selected"] == "openai-answer-luna-v1"


def test_provider_profiles_are_kind_and_price_versioned() -> None:
    answer = get_profile("openai-answer-luna-v1", kind=ProfileKind.ANSWER)
    embedding = get_profile("openai-embedding-3-small-v1", kind=ProfileKind.EMBEDDING)

    assert answer.pricing_verified and answer.price_effective_date == "2026-09-14"
    assert answer.stores_response is False
    assert answer.request_timeout_seconds == 60
    assert answer.max_attempts == 1
    assert answer.token_estimator == "utf8_bytes_upper_bound_v1"
    assert embedding.dimension == 1536


def test_custom_endpoint_requires_explicit_compatibility_check(tmp_path) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    override = {
        "endpoint": "http://127.0.0.1:9911/v1/embeddings",
        "auth_slot": "city-proxy",
        "input_usd_per_million": "0.04",
        "output_usd_per_million": None,
        "price_effective_date": "2026-09-14",
        "price_source": "https://example.test/pricing",
        "stores_response": False,
    }
    settings = replace(
        context.settings,
        embedding_profile="openai-embedding-3-small-v1",
        profile_overrides={"openai-embedding-3-small-v1": override},
    ).validate()
    save_local_settings(context.paths, settings)
    context = WorkspaceContext.from_options(context.paths.root, environment={})
    profile = get_configured_profile(context.settings, ProfileKind.EMBEDDING)
    assert profile.provider == "openai-compatible"
    assert profile.credential_slot == "city-proxy"
    assert profile.compatibility_verified is False

    storage = LocalStorage.open(context.paths, initialize=True)
    resolver = CredentialResolver(context, environment={})
    resolver.secret_file.set("city-proxy", "sk-custom-endpoint-fixture")
    blocked_client = httpx.Client()
    gateway = ProviderGateway(context, UsageLedger(storage), client=blocked_client)
    try:
        with pytest.raises(ProviderExecutionError, match="compatibility check"):
            gateway.embeddings(
                inputs=["blocked before network"],
                profile=profile,
                credential="sk-custom-endpoint-fixture",
            )
    finally:
        gateway.close()
        blocked_client.close()

    estimate = profile_compatibility_estimate(context, ProfileKind.EMBEDDING)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == override["endpoint"]
        assert request.headers["Authorization"] == "Bearer sk-custom-endpoint-fixture"
        return httpx.Response(
            200,
            request=request,
            json={
                "data": [{"index": 0, "embedding": [0.0] * 1536}],
                "usage": {"prompt_tokens": estimate.estimated_input_tokens},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    checked_gateway = ProviderGateway(context, UsageLedger(storage), client=client)
    try:
        result = run_profile_compatibility_check(
            context,
            storage,
            ProfileKind.EMBEDDING,
            approve_cost=True,
            max_cost_usd=estimate.estimated_cost_usd,
            gateway=checked_gateway,
            credentials=resolver,
        )
    finally:
        checked_gateway.close()
        client.close()
        storage.close()
    assert result.status == "compatible"
    reloaded = WorkspaceContext.from_options(context.paths.root, environment={})
    verified = get_configured_profile(reloaded.settings, ProfileKind.EMBEDDING)
    assert verified.id == result.effective_profile_id
    assert verified.compatibility_verified is True


def test_custom_answer_endpoint_uses_responses_contract_and_separate_slot(
    tmp_path,
) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "answer-workspace", environment={}, initialize=True
    )
    settings = replace(
        context.settings,
        answer_profile="openai-answer-luna-v1",
        profile_overrides={
            "openai-answer-luna-v1": {
                "endpoint": "https://gateway.example/v1/responses",
                "auth_slot": "answer-gateway",
                "input_usd_per_million": "0.30",
                "output_usd_per_million": "1.50",
                "price_effective_date": "2026-09-14",
                "price_source": "https://gateway.example/pricing",
                "stores_response": True,
            }
        },
    ).validate()
    save_local_settings(context.paths, settings)
    context = WorkspaceContext.from_options(context.paths.root, environment={})
    storage = LocalStorage.open(context.paths, initialize=True)
    resolver = CredentialResolver(context, environment={})
    resolver.secret_file.set("answer-gateway", "sk-answer-gateway-fixture")
    estimate = profile_compatibility_estimate(context, ProfileKind.ANSWER)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://gateway.example/v1/responses"
        body = json.loads(request.content)
        assert body["model"] == "gpt-5.6-luna"
        assert body["store"] is False
        return httpx.Response(
            200,
            request=request,
            json={
                "output_text": "compatible [E1]",
                "usage": {"input_tokens": 25, "output_tokens": 4},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(context, UsageLedger(storage), client=client)
    try:
        result = run_profile_compatibility_check(
            context,
            storage,
            ProfileKind.ANSWER,
            approve_cost=True,
            max_cost_usd=estimate.estimated_cost_usd,
            gateway=gateway,
            credentials=resolver,
        )
    finally:
        gateway.close()
        client.close()
        storage.close()
    assert result.status == "compatible"
    assert result.stores_response is True
    assert result.output_tokens == 4


def test_cli_configures_separate_advanced_credential_slot(
    tmp_path, monkeypatch, capsys
) -> None:
    root = tmp_path / "advanced-workspace"
    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--data-dir",
                str(root),
                "profiles",
                "select",
                "embedding",
                "openai-embedding-3-small-v1",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--data-dir",
                str(root),
                "profiles",
                "configure-endpoint",
                "embedding",
                "https://gateway.example/v1/embeddings",
                "--auth-slot",
                "city-proxy",
                "--input-price",
                "0.04",
                "--price-effective-date",
                "2026-09-14",
                "--price-source",
                "https://gateway.example/pricing",
                "--stores-response",
                "no",
                "--json",
            ]
        )
        == 0
    )
    configured = json.loads(capsys.readouterr().out)
    assert configured["credential_slot"] == "city-proxy"
    assert configured["compatibility_verified"] is False

    monkeypatch.setattr("getpass.getpass", lambda _prompt: "sk-private-fixture-value")
    assert (
        main(
            [
                "--data-dir",
                str(root),
                "credentials",
                "set",
                "city-proxy",
                "--storage",
                "file",
                "--json",
            ]
        )
        == 0
    )
    stored = json.loads(capsys.readouterr().out)
    assert stored["provider"] == "city-proxy"
    assert "sk-private" not in json.dumps(stored)

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "profiles",
                "check",
                "embedding",
                "--estimate-only",
                "--json",
            ]
        )
        == 0
    )
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["credential_slot"] == "city-proxy"
    assert estimate["endpoint"] == "https://gateway.example/v1/embeddings"
    settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    assert "sk-private" not in json.dumps(settings)


def test_cli_unknown_pricing_is_explicit_and_compatibility_is_labeled(
    tmp_path, capsys
) -> None:
    root = tmp_path / "unknown-price-workspace"
    assert (
        main(
            [
                "--data-dir",
                str(root),
                "setup",
                "--answer-profile",
                "openai-answer-luna-v1",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--data-dir",
                str(root),
                "profiles",
                "configure-endpoint",
                "answer",
                "https://gateway.example/v1/responses",
                "--pricing-unknown",
                "--stores-response",
                "no",
                "--json",
            ]
        )
        == 0
    )
    configured = json.loads(capsys.readouterr().out)
    assert configured["pricing_verified"] is False
    assert configured["cost_policy"] == "manual_unknown_cost_outside_usd_caps"

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "profiles",
                "check",
                "answer",
                "--estimate-only",
                "--json",
            ]
        )
        == 0
    )
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["estimated_cost_usd"] is None
    assert estimate["cost_known"] is False
    assert "outside USD budget caps" in estimate["warning"]

    settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    override = settings["profile_overrides"]["openai-answer-luna-v1"]
    assert override["input_usd_per_million"] is None
    assert override["output_usd_per_million"] is None
    assert override["price_effective_date"] is None
    assert override["price_source"] is None


def test_custom_profile_rejects_partial_unknown_price_metadata(tmp_path) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "partial-price-workspace", environment={}, initialize=True
    )
    settings = replace(
        context.settings,
        answer_profile="openai-answer-luna-v1",
        profile_overrides={
            "openai-answer-luna-v1": {
                "endpoint": "https://gateway.example/v1/responses",
                "auth_slot": "openai",
                "input_usd_per_million": None,
                "output_usd_per_million": "1.50",
                "price_effective_date": None,
                "price_source": None,
                "stores_response": False,
            }
        },
    )
    with pytest.raises(ValueError, match="fully specified or explicitly unknown"):
        settings.validate()


def test_unknown_price_compatibility_check_requires_explicit_override(
    tmp_path,
) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "unknown-check-workspace", environment={}, initialize=True
    )
    settings = replace(
        context.settings,
        answer_profile="openai-answer-luna-v1",
        profile_overrides={
            "openai-answer-luna-v1": {
                "endpoint": "https://gateway.example/v1/responses",
                "auth_slot": "unknown-gateway",
                "input_usd_per_million": None,
                "output_usd_per_million": None,
                "price_effective_date": None,
                "price_source": None,
                "stores_response": False,
            }
        },
    ).validate()
    save_local_settings(context.paths, settings)
    context = WorkspaceContext.from_options(context.paths.root, environment={})
    storage = LocalStorage.open(context.paths, initialize=True)
    resolver = CredentialResolver(context, environment={})
    resolver.secret_file.set("unknown-gateway", "sk-unknown-price-fixture")
    estimate = profile_compatibility_estimate(context, ProfileKind.ANSWER)
    assert estimate.cost_known is False
    assert estimate.estimated_cost_usd is None

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            request=request,
            json={
                "output_text": "compatible [E1]",
                "usage": {"input_tokens": 25, "output_tokens": 4},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(context, UsageLedger(storage), client=client)
    try:
        with pytest.raises(ValueError, match="allow-unknown-cost"):
            run_profile_compatibility_check(
                context,
                storage,
                ProfileKind.ANSWER,
                approve_cost=False,
                max_cost_usd=None,
                gateway=gateway,
                credentials=resolver,
            )
        assert calls == 0
        result = run_profile_compatibility_check(
            context,
            storage,
            ProfileKind.ANSWER,
            approve_cost=False,
            max_cost_usd=None,
            allow_unknown_cost=True,
            gateway=gateway,
            credentials=resolver,
        )
        assert result.status == "compatible"
        assert result.cost_usd is None
        assert result.cost_known is False
        assert calls == 1
        summary = UsageLedger(storage).summary(
            monthly_cap_usd=Decimal("15"), timezone="America/New_York"
        )
        assert summary.unknown_cost_attempts == 1
    finally:
        gateway.close()
        client.close()
        storage.close()

    reloaded = WorkspaceContext.from_options(context.paths.root, environment={})
    verified = get_configured_profile(reloaded.settings, ProfileKind.ANSWER)
    assert verified.compatibility_verified is True
    assert verified.pricing_verified is False


def test_custom_endpoint_rejects_insecure_non_loopback_http(tmp_path, capsys) -> None:
    root = tmp_path / "invalid-endpoint"
    assert (
        main(
            [
                "--data-dir",
                str(root),
                "setup",
                "--embedding-profile",
                "openai-embedding-3-small-v1",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--data-dir",
                str(root),
                "profiles",
                "configure-endpoint",
                "embedding",
                "http://gateway.example/v1/embeddings",
                "--input-price",
                "0.04",
                "--price-effective-date",
                "2026-09-14",
                "--price-source",
                "https://gateway.example/pricing",
                "--stores-response",
                "no",
                "--json",
            ]
        )
        == 2
    )
    assert "require HTTPS" in capsys.readouterr().err


def test_explicit_credential_validation_is_metered_and_ceiling_bounded(
    tmp_path,
) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    resolver = CredentialResolver(context, environment={})
    resolver.secret_file.set("openai", "sk-explicit-validation-fixture")
    estimate = credential_validation_estimate("openai")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("Bearer sk-")
        return httpx.Response(
            200,
            request=request,
            json={
                "data": [{"index": 0, "embedding": [0.0] * 1536}],
                "usage": {"prompt_tokens": estimate.estimated_input_tokens},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(context, UsageLedger(storage), client=client)
    try:
        with pytest.raises(ValueError, match="explicit cost approval"):
            run_credential_validation(
                context,
                storage,
                "openai",
                approve_cost=False,
                max_cost_usd=Decimal("1"),
                gateway=gateway,
                credentials=resolver,
            )
        result = run_credential_validation(
            context,
            storage,
            "openai",
            approve_cost=True,
            max_cost_usd=estimate.estimated_cost_usd,
            gateway=gateway,
            credentials=resolver,
        )
        assert result.status == "valid"
        assert result.cost_usd == estimate.estimated_cost_usd
        usage = UsageLedger(storage).summary(
            monthly_cap_usd=Decimal("15"),
            timezone="America/New_York",
        )
        assert usage.settled_usd == estimate.estimated_cost_usd
    finally:
        gateway.close()
        client.close()
        storage.close()
