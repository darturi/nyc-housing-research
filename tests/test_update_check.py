import httpx
import pytest

from app.maintenance.updates import UpdateCheckError, check_github_release
from app.workspace.network import NetworkAccessDenied, NetworkPolicy


def test_release_check_reads_metadata_without_applying_update() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "tag_name": "v9.9.9",
                    "html_url": "https://github.com/example/project/releases/tag/v9.9.9",
                    "published_at": "2026-09-14T00:00:00Z",
                    "body": "Migration note fixture.",
                },
            )
        )
    )
    try:
        result = check_github_release(
            "https://github.com/example/project",
            NetworkPolicy(offline=False),
            client=client,
        )
    finally:
        client.close()
    assert result.latest_tag == "v9.9.9"
    assert result.update_available is True
    assert result.action_taken == "none"


def test_release_check_rejects_unverified_shape_and_offline_egress() -> None:
    with pytest.raises(UpdateCheckError):
        check_github_release("https://example.com/owner/repo", NetworkPolicy(False))
    with pytest.raises(NetworkAccessDenied):
        check_github_release(
            "https://github.com/example/project", NetworkPolicy(offline=True)
        )
