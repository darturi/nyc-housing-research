from app.answer.providers import OpenAICompatibleAnswerProvider
from app.retrieval.embeddings import OpenAICompatibleEmbeddingProvider
from app.retrieval.schemas import SearchResult


def test_openai_compatible_embedding_provider_parses_vectors():
    provider = StubEmbeddingProvider()

    vectors = provider.embed_batch(["one", "two"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]


def test_openai_compatible_answer_provider_parses_structured_json():
    provider = StubAnswerProvider()

    result = provider.generate("prompt", "question", [_search_result()])

    assert result.answer_status == "answered"
    assert result.answer_text == "Answer text"
    assert result.cited_chunk_ids == ["chunk-1"]
    assert result.total_token_count == 15


class StubEmbeddingProvider(OpenAICompatibleEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(
            provider_name="openai_compatible",
            model_name="test-embedding",
            api_key="test-key",
            base_url="https://example.test/v1",
            dimension=2,
            timeout_seconds=1,
            max_retries=0,
        )

    def _post_json(self, path: str, payload: dict) -> dict:
        assert path == "/embeddings"
        assert payload["input"] == ["one", "two"]
        return {
            "data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ]
        }


class StubAnswerProvider(OpenAICompatibleAnswerProvider):
    def __init__(self) -> None:
        super().__init__(
            provider_name="openai_compatible",
            model_name="test-answer",
            api_key="test-key",
            base_url="https://example.test/v1",
            timeout_seconds=1,
            max_retries=0,
            max_output_tokens=100,
            temperature=0,
        )

    def _post_json(self, path: str, payload: dict) -> dict:
        assert path == "/chat/completions"
        assert payload["response_format"] == {"type": "json_object"}
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"answer_status":"answered","answer":"Answer text",'
                            '"cited_chunk_ids":["chunk-1"]}'
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }


def _search_result() -> SearchResult:
    return SearchResult(
        chunk_id="chunk-1",
        document_id="document-id",
        source_id="source-id",
        source_version_id="source-version-id",
        source_name="NYC Housing Maintenance Code",
        source_type="law",
        jurisdiction="NYC",
        source_url="https://example.test/source",
        citation="NYC Admin Code § 27-2005",
        title="Duties of owner",
        text="The owner shall keep the premises in good repair.",
        score=1.0,
        match_type="citation",
    )
