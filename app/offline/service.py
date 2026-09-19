from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path

from sqlalchemy import delete, func, select

from app.corpus.service import CorpusService
from app.storage.database import LocalStorage
from app.storage.schema import extension_installations, property_cache
from app.workspace.context import WorkspaceContext


class OfflineExtensionError(RuntimeError):
    pass


class OfflineExtensionService:
    """Report independently verifiable offline capabilities without fake readiness."""

    def __init__(self, context: WorkspaceContext, storage: LocalStorage) -> None:
        self._context = context
        self._storage = storage
        self._catalog = _catalog()

    def readiness(self) -> dict[str, object]:
        corpus = CorpusService(self._storage).status()
        local_model = self._active("local_model")
        snapshot = self._active("hpd_snapshot")
        model_check = self._installation_check(local_model, "local_model")
        snapshot_check = self._installation_check(snapshot, "hpd_snapshot")
        with self._storage.state_engine.connect() as connection:
            cached_pages = int(
                connection.scalar(select(func.count()).select_from(property_cache)) or 0
            )
        legal_ready = bool(corpus.active_generation_id)
        capabilities = {
            "legal_keyword_search": {
                "state": "ready" if legal_ready else "blocked",
                "reason_code": None if legal_ready else "legal_corpus_not_installed",
                "generation_id": corpus.active_generation_id,
                "network_required": False,
            },
            "local_cited_answers": model_check,
            "local_semantic_search": (
                {
                    **model_check,
                    "state": "blocked",
                    "reason_code": "compatible_local_embedding_index_not_verified",
                }
                if model_check["state"] != "ready"
                or not local_model.get("manifest", {}).get(
                    "embedding_evaluation_passed"
                )
                else model_check
            ),
            "offline_hpd_snapshot": snapshot_check,
            "cached_hpd_pages": {
                "state": "ready" if cached_pages else "empty",
                "cached_page_count": cached_pages,
                "coverage": "bounded_exact-query_cache",
                "complete_snapshot": False,
            },
        }
        return {
            "offline_enabled": self._context.settings.offline,
            "no_remote_fallback": True,
            "loopback_policy": {
                "enabled": self._context.settings.local_runtime_enabled,
                "allowed_urls": list(self._context.network.allowed_loopback_urls),
                "literal_address_required": True,
                "redirects_allowed": False,
                "environment_proxies_used_by_managed_gateway": False,
            },
            "capabilities": capabilities,
            "fully_offline_research_ready": all(
                capabilities[name]["state"] == "ready"
                for name in (
                    "legal_keyword_search",
                    "local_cited_answers",
                    "offline_hpd_snapshot",
                )
            ),
            "extensions": self.list_extensions(),
            "limitations": [
                "Application URL policy does not control egress by an independently "
                "running model process.",
                "Ordinary backups retain extension manifests but exclude "
                "replaceable bulk assets.",
                "No local model or HPD snapshot is advertised until its pinned "
                "manifest passes all checks.",
            ],
        }

    def list_extensions(self) -> list[dict[str, object]]:
        with self._storage.state_engine.connect() as connection:
            rows = list(
                connection.execute(
                    select(extension_installations).order_by(
                        extension_installations.c.created_at.desc()
                    )
                ).mappings()
            )
        installed_by_kind = {}
        for row in rows:
            installed_by_kind.setdefault(row["kind"], []).append(_installation(row))
        return [
            {
                **entry,
                "installations": installed_by_kind.get(entry["kind"], []),
            }
            for entry in self._catalog["extensions"]
        ]

    def check(self, extension_id: str) -> dict[str, object]:
        entry = self._entry(extension_id)
        installation = self._active(entry["kind"])
        if not installation:
            return {
                "extension_id": extension_id,
                "state": "blocked",
                "reason_code": entry["reason_code"],
                "message": entry["description"],
                "download_attempted": False,
            }
        return {
            "extension_id": extension_id,
            **self._installation_check(installation, entry["kind"]),
            "download_attempted": False,
        }

    def install_unavailable(self, extension_id: str) -> dict[str, object]:
        entry = self._entry(extension_id)
        if entry["installable"]:
            raise OfflineExtensionError("Managed installer is not implemented.")
        return {
            "extension_id": extension_id,
            "state": "blocked",
            "reason_code": entry["reason_code"],
            "message": entry["description"],
            "download_attempted": False,
            "existing_capabilities_unchanged": True,
        }

    def removal(self, extension_id: str, *, apply: bool = False) -> dict[str, object]:
        entry = self._entry(extension_id)
        with self._storage.state_engine.begin() as connection:
            rows = list(
                connection.execute(
                    select(extension_installations).where(
                        extension_installations.c.kind == entry["kind"]
                    )
                ).mappings()
            )
            preview = {
                "extension_id": extension_id,
                "installation_count": len(rows),
                "size_bytes": sum(row["size_bytes"] for row in rows),
                "saved_research_deleted": 0,
                "apply_required": True,
            }
            if not apply:
                return preview
            paths = [row["artifact_uri"] for row in rows if row["artifact_uri"]]
            connection.execute(
                delete(extension_installations).where(
                    extension_installations.c.kind == entry["kind"]
                )
            )
        for relative in paths:
            path = self._safe_extension_path(str(relative))
            if path.is_file():
                path.unlink()
        preview |= {"status": "removed", "apply_required": False}
        return preview

    def snapshots(self) -> list[dict[str, object]]:
        with self._storage.state_engine.connect() as connection:
            rows = connection.execute(
                select(extension_installations).where(
                    extension_installations.c.kind == "hpd_snapshot"
                )
            ).mappings()
            return [_installation(row) for row in rows]

    def analytics_unavailable(self) -> dict[str, object]:
        active = self._active("hpd_snapshot")
        check = self._installation_check(active, "hpd_snapshot")
        if check["state"] != "ready":
            return {
                "state": "blocked",
                "reason_code": check["reason_code"],
                "rows": [],
                "generated_sql_used": False,
            }
        return {
            "state": "blocked",
            "reason_code": "reviewed_analytics_adapter_not_installed",
            "rows": [],
            "generated_sql_used": False,
        }

    def _active(self, kind: str) -> dict[str, object]:
        with self._storage.state_engine.connect() as connection:
            row = (
                connection.execute(
                    select(extension_installations)
                    .where(
                        extension_installations.c.kind == kind,
                        extension_installations.c.active.is_(True),
                    )
                    .order_by(extension_installations.c.updated_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return _installation(row) if row else {}

    def _installation_check(
        self, installation: dict[str, object], kind: str
    ) -> dict[str, object]:
        if not installation:
            entry = next(
                item for item in self._catalog["extensions"] if item["kind"] == kind
            )
            return {
                "state": "blocked",
                "reason_code": entry["reason_code"],
                "installation": None,
            }
        manifest = installation["manifest"]
        required_pass = (
            manifest.get("evaluation_status") == "passed"
            if kind == "local_model"
            else manifest.get("completeness_status") == "verified_complete"
        )
        if not required_pass:
            return {
                "state": "blocked",
                "reason_code": (
                    "model_evaluation_not_passed"
                    if kind == "local_model"
                    else "snapshot_completeness_not_verified"
                ),
                "installation": installation,
            }
        relative = installation.get("artifact_uri")
        if not relative:
            return {
                "state": "blocked",
                "reason_code": "extension_artifact_missing",
                "installation": installation,
            }
        path = self._safe_extension_path(str(relative))
        if not path.is_file():
            return {
                "state": "blocked",
                "reason_code": "extension_artifact_missing",
                "installation": installation,
            }
        expected = manifest.get("sha256")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected != actual:
            return {
                "state": "blocked",
                "reason_code": "extension_checksum_mismatch",
                "installation": installation,
            }
        return {"state": "ready", "reason_code": None, "installation": installation}

    def _entry(self, extension_id: str) -> dict[str, object]:
        for entry in self._catalog["extensions"]:
            if entry["id"] == extension_id:
                return entry
        raise OfflineExtensionError("Unknown offline extension.")

    def _safe_extension_path(self, relative: str) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise OfflineExtensionError("Extension artifact path is unsafe.")
        path = (self._context.paths.root / candidate).resolve()
        try:
            path.relative_to(self._context.paths.extensions.resolve())
        except ValueError as exc:
            raise OfflineExtensionError(
                "Extension artifact escapes its storage root."
            ) from exc
        return path


def _catalog() -> dict[str, object]:
    resource = files("app.resources").joinpath("offline/extensions.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("format_version") != 1 or len(payload.get("extensions", [])) != 2:
        raise OfflineExtensionError("Packaged offline extension catalog is invalid.")
    return payload


def _installation(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "extension_id": row["extension_id"],
        "kind": row["kind"],
        "version": row["version"],
        "state": row["state"],
        "manifest": json.loads(row["manifest_json"]),
        "artifact_uri": row["artifact_uri"],
        "size_bytes": row["size_bytes"],
        "active": bool(row["active"]),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }
