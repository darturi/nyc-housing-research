from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path


def load_legal_review_cases(path: Path | None = None) -> dict:
    if path is None:
        resource = files("app.resources").joinpath(
            "evaluation/legal_review_cases.json"
        )
        payload = json.loads(resource.read_text(encoding="utf-8"))
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        raise ValueError("Legal review fixture format is unsupported.")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 28:
        raise ValueError("Legal review fixture must contain exactly 28 cases.")
    ids = set()
    for item in cases:
        if (
            not isinstance(item, dict)
            or not item.get("id")
            or not item.get("question")
            or item.get("route") not in {"legal", "property"}
            or not item.get("expected_behavior")
            or not isinstance(item.get("required_propositions"), list)
        ):
            raise ValueError("Legal review case schema is invalid.")
        ids.add(item["id"])
    if len(ids) != len(cases):
        raise ValueError("Legal review fixture contains duplicate case IDs.")
    return payload
