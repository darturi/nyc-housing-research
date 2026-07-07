import hashlib
import math
from abc import ABC, abstractmethod
from time import sleep

import httpx

from app.core.config import get_settings


class EmbeddingProviderError(Exception):
    """Raised when an embedding provider cannot return usable vectors."""


class EmbeddingProvider(ABC):
    provider_name: str

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Generate one embedding vector."""

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of texts."""


class FakeEmbeddingProvider(EmbeddingProvider):
    provider_name = "fake"

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    def embed_text(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        words = text.lower().split()
        for word in words:
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            index = digest[0] % self.dimension
            value = (int.from_bytes(digest[1:3], "big") / 65535.0) + 0.01
            vector[index] += value
        return normalize_vector(vector)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(text) for text in texts]


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        provider_name: str,
        model_name: str,
        api_key: str,
        base_url: str,
        dimension: int,
        timeout_seconds: int,
        max_retries: int,
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def embed_text(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.model_name, "input": texts}
        response_json = self._post_json("/embeddings", payload)
        try:
            data = sorted(response_json["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in data]
        except (KeyError, TypeError) as exc:
            raise EmbeddingProviderError("Embedding response was not valid.") from exc
        if len(vectors) != len(texts):
            raise EmbeddingProviderError(
                "Embedding response count did not match input."
            )
        for vector in vectors:
            if len(vector) != self.dimension:
                raise EmbeddingProviderError(
                    "Embedding dimension did not match EMBEDDING_DIMENSION."
                )
        return vectors

    def _post_json(self, path: str, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        f"{self.base_url}{path}",
                        headers=headers,
                        json=payload,
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    sleep(0.25 * (attempt + 1))
                    continue
        raise EmbeddingProviderError(
            "Embedding provider request failed."
        ) from last_error


def normalize_vector(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.embedding_provider == "fake":
        return FakeEmbeddingProvider(settings.embedding_dimension)
    if settings.embedding_provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleEmbeddingProvider(
            provider_name=settings.embedding_provider,
            model_name=settings.embedding_model,
            api_key=settings.embedding_api_key.get_secret_value(),
            base_url=settings.embedding_base_url,
            dimension=settings.embedding_dimension,
            timeout_seconds=settings.embedding_timeout_seconds,
            max_retries=settings.embedding_max_retries,
        )
    raise EmbeddingProviderError(
        f"Unsupported embedding provider: {settings.embedding_provider}"
    )
