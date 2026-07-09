import json
import re
from abc import ABC, abstractmethod
from time import sleep

import httpx

from app.answer.schemas import ProviderAnswer
from app.core.config import get_settings
from app.retrieval.schemas import SearchResult


class LLMProviderError(Exception):
    """Raised when an answer provider cannot produce a response."""


class AnswerProvider(ABC):
    provider_name: str
    model_name: str

    @abstractmethod
    def generate(
        self,
        prompt: str,
        question: str,
        chunks: list[SearchResult],
    ) -> ProviderAnswer:
        """Generate structured answer output from retrieved chunks."""


class FakeAnswerProvider(AnswerProvider):
    provider_name = "fake"

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def generate(
        self,
        prompt: str,
        question: str,
        chunks: list[SearchResult],
    ) -> ProviderAnswer:
        prompt_tokens = _estimate_tokens(prompt)
        if not chunks or _looks_unsupported(question):
            answer_text = (
                "The current corpus does not contain enough retrieved "
                "public-source material to answer that question."
            )
            return ProviderAnswer(
                answer_text=answer_text,
                cited_chunk_ids=[],
                answer_status="unsupported",
                prompt_token_count=prompt_tokens,
                completion_token_count=_estimate_tokens(answer_text),
                total_token_count=prompt_tokens + _estimate_tokens(answer_text),
            )

        first = chunks[0]
        answer_text = build_fake_answer(first)
        completion_tokens = _estimate_tokens(answer_text)
        return ProviderAnswer(
            answer_text=answer_text,
            cited_chunk_ids=[first.chunk_id],
            answer_status="answered",
            prompt_token_count=prompt_tokens,
            completion_token_count=completion_tokens,
            total_token_count=prompt_tokens + completion_tokens,
        )


class OpenAICompatibleAnswerProvider(AnswerProvider):
    def __init__(
        self,
        provider_name: str,
        model_name: str,
        api_key: str,
        base_url: str,
        timeout_seconds: int,
        max_retries: int,
        max_output_tokens: int,
        temperature: float,
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature

    def generate(
        self,
        prompt: str,
        question: str,
        chunks: list[SearchResult],
    ) -> ProviderAnswer:
        if not chunks:
            answer_text = (
                "The current corpus does not contain enough retrieved "
                "public-source material to answer that question."
            )
            return ProviderAnswer(
                answer_text=answer_text,
                cited_chunk_ids=[],
                answer_status="unsupported",
                prompt_token_count=_estimate_tokens(prompt),
                completion_token_count=_estimate_tokens(answer_text),
                total_token_count=_estimate_tokens(prompt)
                + _estimate_tokens(answer_text),
            )

        response_json = self._post_json(
            "/chat/completions",
            {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return only valid JSON with keys: answer_status "
                            "('answered' or 'unsupported'), answer, and "
                            "cited_chunk_ids. Cite only chunk IDs from the "
                            "provided context."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": self.temperature,
                "max_tokens": self.max_output_tokens,
                "response_format": {"type": "json_object"},
            },
        )
        content = _choice_content(response_json)
        parsed = _parse_provider_content(content)
        usage = response_json.get("usage", {})
        answer_text = parsed["answer"]
        cited_chunk_ids = parsed["cited_chunk_ids"]
        answer_status = parsed["answer_status"]
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        return ProviderAnswer(
            answer_text=answer_text,
            cited_chunk_ids=cited_chunk_ids,
            answer_status=answer_status,
            prompt_token_count=prompt_tokens,
            completion_token_count=completion_tokens,
            total_token_count=total_tokens,
        )

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
        raise LLMProviderError("Answer provider request failed.") from last_error


def get_answer_provider() -> AnswerProvider:
    settings = get_settings()
    if settings.answer_llm_provider == "fake":
        return FakeAnswerProvider(settings.answer_llm_model)
    if settings.answer_llm_provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleAnswerProvider(
            provider_name=settings.answer_llm_provider,
            model_name=settings.answer_llm_model,
            api_key=settings.answer_llm_api_key.get_secret_value(),
            base_url=settings.answer_llm_base_url,
            timeout_seconds=settings.answer_llm_timeout_seconds,
            max_retries=settings.answer_llm_max_retries,
            max_output_tokens=settings.answer_max_output_tokens,
            temperature=settings.answer_temperature,
        )
    raise LLMProviderError(
        f"Unsupported answer provider: {settings.answer_llm_provider}"
    )


def build_fake_answer(chunk: SearchResult) -> str:
    citation = chunk.citation or chunk.source_name
    title = clean_title(chunk.title)
    text = normalize_answer_text(chunk.text)
    body = strip_section_heading(text, title)
    clauses = extract_legal_clause_summaries(body)
    guidance_items = (
        extract_guidance_item_summaries(body)
        if chunk.source_type == "guidance"
        else []
    )
    sentences = extract_sentence_summaries(body)

    parts = [f"Based on {citation}, {title}."]
    if clauses:
        parts.append(f"It addresses: {'; '.join(clauses)}.")
    elif guidance_items:
        parts.append(f"It includes: {'; '.join(guidance_items)}.")
    elif sentences:
        parts.append(" ".join(sentences))
    parts.append("This response is limited to the retrieved public-source material.")
    return " ".join(parts)


def clean_title(title: str | None) -> str:
    normalized = normalize_answer_text(title or "the retrieved source")
    return normalized.rstrip(".")


def normalize_answer_text(text: str) -> str:
    return " ".join(text.split())


def strip_section_heading(text: str, title: str) -> str:
    body = re.sub(r"^§+\s*\d+(?:-\d+)?[a-zA-Z]?\s*", "", text).strip()
    title_pattern = re.escape(title.rstrip("."))
    body = re.sub(rf"^{title_pattern}\.?\s*", "", body, flags=re.IGNORECASE).strip()
    return body


def extract_legal_clause_summaries(text: str, limit: int = 5) -> list[str]:
    summaries: list[str] = []
    seen: set[str] = set()
    for label, clause_text in labeled_clauses(text):
        normalized_label = label.lower()
        if normalized_label in seen:
            continue
        summary = truncate_words(clause_summary_text(clause_text), 34)
        if not summary:
            continue
        seen.add(normalized_label)
        summaries.append(f"{label} {summary}")
        if len(summaries) >= limit:
            break
    return summaries


def labeled_clauses(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"(?:(?<=^)|(?<=\s))(?P<label>[a-z]\.|\(\d+[a-z]?\)|\d+[a-z]?\.)\s+"
        r"(?P<body>.*?)(?=\s(?:[a-z]\.|\(\d+[a-z]?\)|\d+[a-z]?\.)\s+|$)",
        flags=re.IGNORECASE,
    )
    clauses: list[tuple[str, str]] = []
    for match in pattern.finditer(text):
        label = match.group("label")
        body = normalize_answer_text(match.group("body")).strip()
        if not body:
            continue
        clauses.append((label, body))
    return clauses


def clause_summary_text(text: str) -> str:
    subclauses = labeled_clauses(text)
    if subclauses:
        return "; ".join(
            f"{label} {clean_clause_summary(first_sentence(body))}"
            for label, body in subclauses[:3]
        )
    return clean_clause_summary(first_sentence(text))


def clean_clause_summary(text: str) -> str:
    summary = normalize_answer_text(text).strip()
    summary = re.sub(r"\s*;?\s+and$", "", summary, flags=re.IGNORECASE)
    return summary.rstrip(" ;:")


def extract_numbered_clause_summaries(text: str, limit: int = 5) -> list[str]:
    summaries: list[str] = []
    seen: set[str] = set()
    for label, clause_text in labeled_clauses(text):
        if not re.fullmatch(r"\d+[a-z]?\.", label, flags=re.IGNORECASE):
            continue
        label_key = label.lower()
        if label_key in seen:
            continue
        summary = truncate_words(first_sentence(clause_text), 24)
        if not summary:
            continue
        seen.add(label_key)
        summaries.append(f"{label} {summary}")
        if len(summaries) >= limit:
            break
    return summaries


GUIDANCE_ACTION_WORDS = (
    "Learn",
    "Certify",
    "Apply",
    "Find",
    "Report",
    "File",
    "Get",
    "Read",
    "Use",
    "Submit",
)
GUIDANCE_ACTION_PATTERN = "|".join(GUIDANCE_ACTION_WORDS)
GUIDANCE_ITEM_START_PATTERN = re.compile(
    rf"(?<=\.)\s+(?=(?:eCertification|[A-Z0-9][\w'()/&.-]*)"
    rf"(?:\s+(?:and|or|of|to|for|the|a|an|in|on|with|"
    rf"eCertification|[A-Z0-9][\w'()/&.-]*)){{0,8}}"
    rf"\s+(?:{GUIDANCE_ACTION_PATTERN})\b)"
)
GUIDANCE_ITEM_PATTERN = re.compile(
    rf"^(?P<title>.+?)\s+(?P<body>(?:{GUIDANCE_ACTION_PATTERN})\b.+)$"
)


def extract_guidance_item_summaries(text: str, limit: int = 5) -> list[str]:
    summaries: list[str] = []
    seen: set[str] = set()
    for item in split_guidance_items(text):
        match = GUIDANCE_ITEM_PATTERN.match(item)
        if not match:
            continue
        title = clean_guidance_item_title(match.group("title"))
        if not title or title.lower() in seen:
            continue
        description = truncate_words(first_sentence(match.group("body")), 18)
        summaries.append(f"{title}: {description}" if description else title)
        seen.add(title.lower())
        if len(summaries) >= limit:
            break
    return summaries


def split_guidance_items(text: str) -> list[str]:
    return [
        item.strip(" .;:")
        for item in GUIDANCE_ITEM_START_PATTERN.split(normalize_answer_text(text))
        if item.strip(" .;:")
    ]


def clean_guidance_item_title(title: str) -> str:
    return normalize_answer_text(title).strip(" .;:")


def extract_sentence_summaries(text: str, limit: int = 2) -> list[str]:
    sentences = [
        truncate_words(sentence, 30)
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if sentence.strip()
    ]
    return [sentence for sentence in sentences if sentence][:limit]


def first_sentence(text: str) -> str:
    protected = (
        normalize_answer_text(text)
        .strip()
        .replace("a.m.", "a.m<period>")
        .replace("p.m.", "p.m<period>")
    )
    sentence = re.split(r"(?<=[.!?])\s+", protected)[0]
    return sentence.replace("a.m<period>", "a.m.").replace("p.m<period>", "p.m.")


def truncate_words(text: str, limit: int) -> str:
    words = normalize_answer_text(text).split()
    if len(words) <= limit:
        return " ".join(words).rstrip(".")
    return " ".join(words[:limit]).rstrip(".,;:") + "..."


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))


def _looks_unsupported(question: str) -> bool:
    normalized = question.lower()
    unsupported_terms = (
        "unpublished",
        "westlaw",
        "lexis",
        "case law",
        "court hold",
        "court held",
        "specific case",
    )
    return any(term in normalized for term in unsupported_terms)


def _choice_content(response_json: dict) -> str:
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMProviderError("Answer provider response was not valid.") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMProviderError("Answer provider returned an empty response.")
    return content


def _parse_provider_content(content: str) -> dict:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMProviderError("Answer provider did not return valid JSON.") from exc

    answer_text = parsed.get("answer") or parsed.get("answer_text")
    if not isinstance(answer_text, str) or not answer_text.strip():
        raise LLMProviderError("Answer provider JSON did not include an answer.")

    answer_status = parsed.get("answer_status")
    if answer_status not in {"answered", "unsupported"}:
        answer_status = "unsupported"

    cited_chunk_ids = parsed.get("cited_chunk_ids", [])
    if not isinstance(cited_chunk_ids, list):
        cited_chunk_ids = []

    normalized_ids: list[str] = []
    for value in cited_chunk_ids:
        if isinstance(value, str):
            normalized_ids.append(value)
        elif isinstance(value, dict) and isinstance(value.get("chunk_id"), str):
            normalized_ids.append(value["chunk_id"])

    return {
        "answer": answer_text.strip(),
        "answer_status": answer_status,
        "cited_chunk_ids": normalized_ids,
    }
