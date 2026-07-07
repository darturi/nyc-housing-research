from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status

from app.core.config import get_settings

BODY_METHODS = {"POST", "PUT", "PATCH"}


async def request_size_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if request.url.path == "/health":
        return await call_next(request)

    content_length = request.headers.get("content-length")
    settings = get_settings()
    if content_length is not None:
        try:
            size = int(content_length)
        except ValueError:
            size = 0
        if size > settings.max_request_body_bytes:
            return Response(
                content='{"detail":"Request body too large."}',
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                media_type="application/json",
            )
    if request.method in BODY_METHODS:
        body_parts: list[bytes] = []
        body_size = 0
        async for chunk in request.stream():
            body_size += len(chunk)
            if body_size > settings.max_request_body_bytes:
                return Response(
                    content='{"detail":"Request body too large."}',
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    media_type="application/json",
                )
            body_parts.append(chunk)
        request._body = b"".join(body_parts)  # noqa: SLF001
    return await call_next(request)
