from __future__ import annotations

from app.corpus.manifests import SourcePack, load_source_packs
from app.corpus.service import CorpusService, CorpusValidationError


class SourcePackService:
    def __init__(self, corpus: CorpusService) -> None:
        self._corpus = corpus
        self._packs = load_source_packs()

    def list(self) -> list[dict[str, object]]:
        statuses = {item["slug"]: item for item in self._corpus.source_statuses()}
        return [
            self._payload(pack, statuses)
            for pack in sorted(self._packs.values(), key=lambda item: item.title)
        ]

    def get(self, pack_id: str) -> dict[str, object]:
        pack = self._pack(pack_id)
        statuses = {item["slug"]: item for item in self._corpus.source_statuses()}
        return self._payload(pack, statuses)

    def module_slugs(
        self, pack_id: str, selected: list[str] | None = None
    ) -> list[str]:
        pack = self._pack(pack_id)
        slugs = list(pack.module_slugs if selected is None else selected)
        if not slugs:
            raise CorpusValidationError("Select at least one pack module.")
        invalid = sorted(set(slugs) - set(pack.module_slugs))
        if invalid:
            raise CorpusValidationError(
                f"Module(s) do not belong to pack {pack_id}: " + ", ".join(invalid)
            )
        return list(dict.fromkeys(slugs))

    def removal_preview(
        self, pack_id: str, selected: list[str] | None = None
    ) -> dict[str, object]:
        slugs = self.module_slugs(pack_id, selected)
        pack = self.get(pack_id)
        installed = {
            module["slug"] for module in pack["modules"] if module["installed"]
        }
        removed = [slug for slug in slugs if slug in installed]
        return {
            "pack_id": pack_id,
            "selected_modules": slugs,
            "installed_modules_to_remove": removed,
            "retained_versions_available": bool(removed),
            "coverage_after_removal": "not_installed" if removed else pack["status"],
            "limitations": pack["limitations"],
            "saved_evidence_impact": (
                "Saved source versions remain retained, but removed modules are no "
                "longer part of active retrieval."
            ),
        }

    def coverage_for_query(self, query: str) -> list[dict[str, object]]:
        normalized = " ".join(query.lower().split())
        notices: list[dict[str, object]] = []
        for pack in self._packs.values():
            terms = pack.raw.get("query_terms", [])
            if not any(
                isinstance(term, str) and term.lower() in normalized for term in terms
            ):
                continue
            payload = self.get(pack.id)
            unavailable = [
                {"slug": module["slug"], "name": module["name"]}
                for module in payload["modules"]
                if not module["installed"]
            ]
            incomplete = bool(unavailable or payload["missing_modules"])
            notices.append(
                {
                    "pack_id": pack.id,
                    "coverage_status": (
                        "partial_topic_coverage" if incomplete else "installed"
                    ),
                    "unavailable_modules": unavailable,
                    "unresolved_catalog_modules": payload["missing_modules"],
                    "limitations": payload["limitations"],
                    "supported_topics": payload["supported_topics"],
                }
            )
        return notices

    def _pack(self, pack_id: str) -> SourcePack:
        pack = self._packs.get(pack_id)
        if pack is None:
            raise CorpusValidationError(f"Unknown source pack: {pack_id}")
        return pack

    @staticmethod
    def _payload(
        pack: SourcePack, statuses: dict[str, dict[str, object]]
    ) -> dict[str, object]:
        modules = [statuses[slug] for slug in pack.module_slugs]
        installed_count = sum(bool(item["installed"]) for item in modules)
        restorable_count = sum(
            not bool(item["installed"]) and bool(item["retained_version_available"])
            for item in modules
        )
        if installed_count == 0:
            status = "not_installed"
        elif installed_count == len(modules):
            status = (
                "installed_partial_coverage" if pack.missing_modules else "installed"
            )
        else:
            status = "partially_installed"
        return {
            "id": pack.id,
            "catalog_version": pack.catalog_version,
            "catalog_verified_at": pack.catalog_verified_at,
            "title": pack.title,
            "description": pack.description,
            "jurisdiction": pack.jurisdiction,
            "support_status": pack.support_status,
            "status": status,
            "supported_topics": list(pack.supported_topics),
            "limitations": list(pack.limitations),
            "missing_modules": list(pack.missing_modules),
            "dependencies": list(pack.dependencies),
            "modules": modules,
            "installed_module_count": installed_count,
            "restorable_module_count": restorable_count,
            "module_count": len(modules),
            "estimated_download_bytes": None,
        }
