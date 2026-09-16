import httpx
import pytest

from app.corpus.download import download_source_artifacts
from app.corpus.service import CorpusService
from app.storage.database import LocalStorage
from app.workspace.context import WorkspaceContext
from app.workspace.network import NetworkAccessDenied


def test_source_download_uses_manifest_and_typed_artifact(tmp_path) -> None:
    seen_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(
            200,
            content=b"section artifact",
            headers={"content-type": "text/plain"},
            request=request,
        )

    context = WorkspaceContext.from_options(tmp_path / "data", environment={})
    artifacts = download_source_artifacts(
        context,
        ["ny-rpapl"],
        transport=httpx.MockTransport(handler),
    )

    assert len(artifacts) == 1
    assert artifacts[0].slug == "ny-rpapl"
    assert artifacts[0].content == b"section artifact"
    assert seen_urls == [
        "https://legislation.nysenate.gov/pdf/laws/RPA?full=true"
    ]


def test_source_download_reuses_validated_artifact_on_conditional_304(
    tmp_path,
) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "data", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    content = b"\xc2\xa7 711. Grounds for summary proceedings."

    def initial_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=content,
            headers={
                "content-type": "text/plain",
                "etag": '"source-v1"',
                "last-modified": "Tue, 15 Sep 2026 12:00:00 GMT",
            },
            request=request,
        )

    first = download_source_artifacts(
        context,
        ["ny-rpapl"],
        transport=httpx.MockTransport(initial_handler),
    )[0]
    generation = CorpusService(storage).install_artifacts(
        [first], allow_partial=True
    )
    before = next(
        item
        for item in CorpusService(storage).source_statuses()
        if item["slug"] == "ny-rpapl"
    )

    def unchanged_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-None-Match"] == '"source-v1"'
        assert request.headers["If-Modified-Since"] == (
            "Tue, 15 Sep 2026 12:00:00 GMT"
        )
        return httpx.Response(304, request=request)

    unchanged = download_source_artifacts(
        context,
        ["ny-rpapl"],
        transport=httpx.MockTransport(unchanged_handler),
    )[0]
    assert unchanged.content == content
    assert unchanged.etag == '"source-v1"'
    assert unchanged.last_modified == "Tue, 15 Sep 2026 12:00:00 GMT"
    assert (
        CorpusService(storage).install_artifacts([unchanged], allow_partial=True)
        == generation
    )
    after = next(
        item
        for item in CorpusService(storage).source_statuses()
        if item["slug"] == "ny-rpapl"
    )
    assert after["retrieved_at"] == before["retrieved_at"]
    assert after["last_checked_at"] >= before["last_checked_at"]
    storage.close()


def test_offline_source_download_is_blocked_before_transport(tmp_path) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"unexpected", request=request)

    context = WorkspaceContext.from_options(
        tmp_path / "data",
        environment={"NYC_HOUSING_OFFLINE": "true"},
    )
    with pytest.raises(NetworkAccessDenied):
        download_source_artifacts(
            context,
            ["ny-rpapl"],
            transport=httpx.MockTransport(handler),
        )
    assert called is False
