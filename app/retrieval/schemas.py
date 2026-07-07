from dataclasses import dataclass


@dataclass
class SearchFilters:
    source_type: str | None = None
    jurisdiction: str | None = None
    source_slug: str | None = None
    source_id: str | None = None
    document_id: str | None = None

    @classmethod
    def from_dict(cls, values: dict | None) -> "SearchFilters":
        values = values or {}
        allowed = set(cls.__dataclass_fields__)
        unsupported = set(values) - allowed
        if unsupported:
            raise ValueError(f"Unsupported filters: {', '.join(sorted(unsupported))}")
        return cls(**values)

    def as_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "source_type": self.source_type,
                "jurisdiction": self.jurisdiction,
                "source_slug": self.source_slug,
                "source_id": self.source_id,
                "document_id": self.document_id,
            }.items()
            if value is not None
        }


@dataclass
class SearchResult:
    chunk_id: str
    document_id: str
    source_id: str
    source_version_id: str
    source_name: str
    source_type: str
    jurisdiction: str
    source_url: str
    citation: str | None
    title: str | None
    text: str
    score: float
    match_type: str

