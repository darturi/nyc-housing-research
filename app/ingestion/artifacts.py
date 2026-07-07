import re
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import get_settings

SAFE_EXTENSION_PATTERN = re.compile(r"^[a-zA-Z0-9]+$")
SAFE_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def artifact_root() -> Path:
    return Path(get_settings().artifact_storage_path).resolve()


def ensure_artifact_storage() -> Path:
    root = artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def sanitize_source_slug(source_slug: str) -> str:
    if not SAFE_SLUG_PATTERN.fullmatch(source_slug):
        raise ValueError("Invalid source slug for artifact path.")
    return source_slug


def sanitize_extension(extension: str) -> str:
    normalized = extension.lower().lstrip(".") or "bin"
    if not SAFE_EXTENSION_PATTERN.fullmatch(normalized):
        raise ValueError("Invalid artifact extension.")
    return normalized


def write_artifact(
    source_slug: str,
    content_hash: str,
    content_bytes: bytes,
    extension: str,
) -> str:
    settings = get_settings()
    if settings.artifact_storage_backend == "s3":
        return write_s3_artifact(source_slug, content_hash, content_bytes, extension)

    root = ensure_artifact_storage()
    safe_slug = sanitize_source_slug(source_slug)
    safe_extension = sanitize_extension(extension)
    source_dir = root / "sources" / safe_slug
    source_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = (source_dir / f"{content_hash}.{safe_extension}").resolve()
    artifact_path.relative_to(root)
    if not artifact_path.exists():
        artifact_path.write_bytes(content_bytes)
    return str(artifact_path)


def read_artifact(artifact_uri: str) -> bytes:
    if artifact_uri.startswith("s3://"):
        return read_s3_artifact(artifact_uri)
    path = Path(artifact_uri).resolve()
    path.relative_to(artifact_root())
    return path.read_bytes()


def artifact_exists(artifact_uri: str) -> bool:
    if artifact_uri.startswith("s3://"):
        return s3_artifact_exists(artifact_uri)
    try:
        path = Path(artifact_uri).resolve()
        path.relative_to(artifact_root())
    except ValueError:
        return False
    return path.exists()


def write_s3_artifact(
    source_slug: str,
    content_hash: str,
    content_bytes: bytes,
    extension: str,
) -> str:
    settings = get_settings()
    safe_slug = sanitize_source_slug(source_slug)
    safe_extension = sanitize_extension(extension)
    key = s3_artifact_key(safe_slug, content_hash, safe_extension)
    client = s3_client()
    client.put_object(
        Bucket=settings.artifact_s3_bucket,
        Key=key,
        Body=content_bytes,
        ContentType=content_type_for_extension(safe_extension),
    )
    return f"s3://{settings.artifact_s3_bucket}/{key}"


def read_s3_artifact(artifact_uri: str) -> bytes:
    bucket, key = parse_s3_uri(artifact_uri)
    response = s3_client().get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def s3_artifact_exists(artifact_uri: str) -> bool:
    bucket, key = parse_s3_uri(artifact_uri)
    try:
        s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def parse_s3_uri(artifact_uri: str) -> tuple[str, str]:
    parsed = urlparse(artifact_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError("Invalid S3 artifact URI.")
    return parsed.netloc, parsed.path.lstrip("/")


def s3_artifact_key(source_slug: str, content_hash: str, extension: str) -> str:
    settings = get_settings()
    prefix = settings.artifact_s3_prefix.strip("/")
    path = f"sources/{source_slug}/{content_hash}.{extension}"
    return f"{prefix}/{path}" if prefix else path


def content_type_for_extension(extension: str) -> str:
    return {
        "html": "text/html",
        "json": "application/json",
        "txt": "text/plain",
    }.get(extension, "application/octet-stream")


def s3_client():
    settings = get_settings()
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required when ARTIFACT_STORAGE_BACKEND=s3."
        ) from exc

    kwargs = {}
    if settings.artifact_s3_region:
        kwargs["region_name"] = settings.artifact_s3_region
    if settings.artifact_s3_endpoint_url:
        kwargs["endpoint_url"] = settings.artifact_s3_endpoint_url
    if settings.artifact_s3_access_key_id:
        kwargs["aws_access_key_id"] = (
            settings.artifact_s3_access_key_id.get_secret_value()
        )
    if settings.artifact_s3_secret_access_key:
        kwargs["aws_secret_access_key"] = (
            settings.artifact_s3_secret_access_key.get_secret_value()
        )
    return boto3.client("s3", **kwargs)
