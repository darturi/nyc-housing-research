from types import SimpleNamespace

from app.core.config import get_settings
from app.ingestion import artifacts


def test_s3_artifact_write_read_and_exists(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "artifact_storage_backend", "s3")
    monkeypatch.setattr(settings, "artifact_s3_bucket", "bucket")
    monkeypatch.setattr(settings, "artifact_s3_prefix", "prefix")
    client = FakeS3Client()
    monkeypatch.setattr(artifacts, "s3_client", lambda: client)

    uri = artifacts.write_artifact("source-slug", "abc123", b"content", "txt")

    assert uri == "s3://bucket/prefix/sources/source-slug/abc123.txt"
    assert artifacts.artifact_exists(uri)
    assert artifacts.read_artifact(uri) == b"content"


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body
        assert ContentType == "text/plain"

    def get_object(self, Bucket, Key):
        return {"Body": SimpleNamespace(read=lambda: self.objects[(Bucket, Key)])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise KeyError(Key)
