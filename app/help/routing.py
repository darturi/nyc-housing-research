from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, date, datetime
from importlib.resources import files
from urllib.parse import urlparse

from sqlalchemy import insert

from app.storage.database import LocalStorage
from app.storage.schema import help_feedback

RULE_VERSION = "urgent-help-v1"
_RULES = (
    (
        "danger-current-v1",
        "danger",
        100,
        re.compile(
            r"\b(fire|smoke|fumes?|gas (?:leak|smell)|carbon monoxide|"
            r"immediate danger|life[- ]threatening|violence|burning)\b",
            re.I,
        ),
    ),
    (
        "lockout-current-v1",
        "lockout",
        90,
        re.compile(
            r"\b(locked (?:me|us) out|landlord (?:changed|change) (?:my |our )?locks?|"
            r"cannot get (?:back )?in|can't get (?:back )?in|shut out of (?:my|our) "
            r"(?:home|apartment))\b",
            re.I,
        ),
    ),
    (
        "essential-services-current-v1",
        "essential_services",
        80,
        re.compile(
            r"\b(no (?:heat|hot water|water|electricity|power|cooking gas)|"
            r"heat (?:is|has been) off|hot water (?:is|has been) off|"
            r"utility shut[- ]?off)\b",
            re.I,
        ),
    ),
    (
        "eviction-court-current-v1",
        "eviction_court",
        75,
        re.compile(
            r"\b(eviction (?:notice|case|court|warrant)|"
            r"housing court (?:date|case|tomorrow|today)|"
            r"marshal(?:'s)? notice|warrant of eviction|motion to restore)\b",
            re.I,
        ),
    ),
    (
        "legal-help-request-v1",
        "legal_help",
        60,
        re.compile(
            r"\b(need (?:a )?(?:housing )?lawyer|free legal help|tenant attorney|"
            r"legal services for tenant|where can i get legal help)\b",
            re.I,
        ),
    ),
)
_HISTORICAL = re.compile(
    r"\b(historically|history of|in \d{4}|used to|research(?:ing)?|law review|"
    r"case study|hypothetical|what does .* mean|quoted?|article about)\b",
    re.I,
)
_NEGATED = re.compile(
    r"\b(not|never|no longer|didn't|did not|wasn't|was not)\b.{0,30}"
    r"(locked out|fire|smoke|no heat|eviction)",
    re.I,
)
_NON_NYC = re.compile(
    r"\b(outside (?:new york city|nyc)|new jersey|boston|chicago|philadelphia|"
    r"los angeles|california|florida|texas)\b",
    re.I,
)


class HelpCatalogError(RuntimeError):
    pass


class HelpResourceService:
    def __init__(self, storage: LocalStorage | None = None) -> None:
        self._storage = storage
        self._catalog = _load_catalog()

    def list(
        self,
        *,
        language: str = "en",
        topic: str | None = None,
        housing_context: str | None = None,
    ) -> dict[str, object]:
        selected_language = language if language in {"en", "es"} else "en"
        cards = [
            item
            for item in self._catalog["cards"]
            if item["language"] == selected_language
            and (topic is None or item["topic"] == topic)
            and _context_matches(item, housing_context)
        ]
        return {
            "catalog_version": self._catalog["catalog_version"],
            "source_checked_at": self._catalog["source_checked_at"],
            "editorial_status": self._catalog["editorial_status"],
            "reviewer_role": self._catalog["reviewer_role"],
            "next_review_date": self._catalog["next_review_date"],
            "language": selected_language,
            "language_fallback": selected_language != language,
            "review_due": date.fromisoformat(self._catalog["next_review_date"])
            < date.today(),
            "cards": sorted(cards, key=lambda item: (-item["priority"], item["id"])),
        }

    def match(
        self,
        question: str,
        *,
        language: str = "en",
        housing_context: str | None = None,
    ) -> dict[str, object]:
        question = question.strip()
        if not question or len(question) > 4_000:
            raise HelpCatalogError("Question must contain 1 to 4,000 characters.")
        non_nyc = bool(_NON_NYC.search(question))
        current_context = not _HISTORICAL.search(question) and not _NEGATED.search(
            question
        )
        matches = []
        for rule_id, topic, priority, pattern in _RULES:
            if pattern.search(question):
                matches.append(
                    {
                        "rule_id": rule_id,
                        "rule_version": RULE_VERSION,
                        "topic": topic,
                        "priority": priority,
                        "urgency": "current_report"
                        if current_context
                        else "related_resource",
                        "rationale": "Deterministic local phrase and context rule.",
                    }
                )
        selected_language = language if language in {"en", "es"} else "en"
        cards = []
        if not non_nyc:
            topics = {item["topic"] for item in matches}
            for item in self._catalog["cards"]:
                if (
                    item["language"] == selected_language
                    and item["topic"] in topics
                    and _context_matches(item, housing_context)
                ):
                    cards.append(item)
        cards.sort(key=lambda item: (-item["priority"], item["id"]))
        return {
            "message": (
                "NYC-specific resources are not presented as applicable because the "
                "question explicitly names another location."
                if non_nyc
                else "Resources for the situation you described."
                if cards
                else "No topic-specific resource was matched; manual help remains "
                "available."
            ),
            "catalog_version": self._catalog["catalog_version"],
            "rule_version": RULE_VERSION,
            "editorial_status": self._catalog["editorial_status"],
            "matched_rules": matches,
            "suggested_card_ids": [item["id"] for item in cards],
            "cards": cards,
            "jurisdiction_caveat": "explicit_non_nyc" if non_nyc else None,
            "eligibility_determined": False,
            "question_retained": False,
        }

    def feedback(
        self, *, rule_ids: list[str], topic: str, helpful: bool
    ) -> dict[str, object]:
        if self._storage is None:
            raise HelpCatalogError("Feedback storage is unavailable.")
        known_rules = {item[0] for item in _RULES}
        if any(rule_id not in known_rules for rule_id in rule_ids):
            raise HelpCatalogError("Feedback contains an unknown routing rule.")
        if topic not in {item[1] for item in _RULES}:
            raise HelpCatalogError("Feedback topic is invalid.")
        feedback_id = str(uuid.uuid4())
        created_at = datetime.now(UTC)
        with self._storage.state_engine.begin() as connection:
            connection.execute(
                insert(help_feedback).values(
                    id=feedback_id,
                    rule_ids_json=json.dumps(sorted(set(rule_ids))),
                    topic=topic,
                    helpful=helpful,
                    created_at=created_at,
                )
            )
        return {
            "id": feedback_id,
            "stored_fields": ["rule_ids", "topic", "helpful", "created_at"],
            "question_stored": False,
            "created_at": created_at.isoformat(),
        }


def _load_catalog() -> dict[str, object]:
    resource = files("app.resources").joinpath("help/catalog.json")
    try:
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HelpCatalogError("Packaged help catalog is unreadable.") from exc
    if payload.get("format_version") != 1 or not isinstance(payload.get("cards"), list):
        raise HelpCatalogError("Packaged help catalog format is unsupported.")
    card_ids = set()
    for card in payload["cards"]:
        required = {
            "id",
            "topic",
            "priority",
            "geography",
            "housing_contexts",
            "language",
            "title",
            "body",
            "actions",
            "sources",
        }
        if (
            not isinstance(card, dict)
            or not required <= card.keys()
            or card["id"] in card_ids
        ):
            raise HelpCatalogError("Packaged help catalog contains an invalid card.")
        card_ids.add(card["id"])
        for action in card["actions"]:
            _validate_action(action)
        for source in card["sources"]:
            _validate_official_url(source)
    canonical = json.dumps(
        payload["cards"], ensure_ascii=False, sort_keys=True
    ).encode()
    payload["content_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def _validate_action(action: object) -> None:
    if not isinstance(action, dict) or set(action) != {"type", "label", "destination"}:
        raise HelpCatalogError("Help card action is invalid.")
    if action["type"] == "official_link":
        _validate_official_url(action["destination"])
    elif action["type"] == "telephone":
        if not re.fullmatch(r"\d{3,15}", str(action["destination"])):
            raise HelpCatalogError("Help card telephone destination is invalid.")
    else:
        raise HelpCatalogError("Help card action type is unsupported.")


def _validate_official_url(value: object) -> None:
    parsed = urlparse(str(value))
    if parsed.scheme != "https" or parsed.hostname not in {
        "www.nyc.gov",
        "portal.311.nyc.gov",
    }:
        raise HelpCatalogError(
            "Help resource URL is not an approved official destination."
        )


def _context_matches(card: dict[str, object], context: str | None) -> bool:
    contexts = card["housing_contexts"]
    if "any" in contexts:
        return True
    selected = context or "unknown"
    return selected in contexts
