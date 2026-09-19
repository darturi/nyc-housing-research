from __future__ import annotations

import json
from importlib.resources import files


class LocaleCatalogError(RuntimeError):
    pass


def load_locale_catalog(locale: str) -> dict[str, object]:
    selected = locale if locale in {"en", "es"} else "en"
    resource = files("app.resources").joinpath(f"locales/{selected}.json")
    try:
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocaleCatalogError("Locale catalog is unreadable.") from exc
    if (
        payload.get("format_version") != 1
        or payload.get("locale") != selected
        or not isinstance(payload.get("messages"), dict)
    ):
        raise LocaleCatalogError("Locale catalog format is invalid.")
    english_resource = files("app.resources").joinpath("locales/en.json")
    english = json.loads(english_resource.read_text(encoding="utf-8"))
    missing = set(english["messages"]) - set(payload["messages"])
    extra = set(payload["messages"]) - set(english["messages"])
    if missing or extra:
        raise LocaleCatalogError(
            "Locale catalog keys do not match the English reference catalog."
        )
    return {
        **payload,
        "requested_locale": locale,
        "fallback": selected != locale,
    }
