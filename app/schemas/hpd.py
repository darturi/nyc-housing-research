from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator


class HpdViolationSearchRequest(BaseModel):
    building_id: str | None = Field(default=None, max_length=120)
    registration_id: str | None = Field(default=None, max_length=120)
    house_number: str | None = Field(default=None, max_length=80)
    street_name: str | None = Field(default=None, max_length=255)
    zip_code: str | None = Field(default=None, max_length=20)
    boro: str | None = Field(default=None, max_length=80)
    violation_class: str | None = Field(default=None, max_length=20)
    current_status: str | None = Field(default=None, max_length=120)
    limit: int | None = Field(default=50, ge=1, validate_default=True)
    offset: int = Field(default=0, ge=0)

    @field_validator(
        "building_id",
        "registration_id",
        "house_number",
        "street_name",
        "zip_code",
        "boro",
        "violation_class",
        "current_status",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("limit")
    @classmethod
    def clamp_limit(cls, value: int | None) -> int:
        if value is None:
            return 50
        return min(value, 100)

    @model_validator(mode="after")
    def require_property_filter(self) -> "HpdViolationSearchRequest":
        has_identifier = bool(self.building_id or self.registration_id)
        has_address = bool(self.house_number and self.street_name)
        if not has_identifier and not has_address:
            raise ValueError(
                "Provide building_id, registration_id, or house_number and "
                "street_name."
            )
        return self


class HpdViolationResponse(BaseModel):
    id: str
    external_id: str
    building_id: str | None
    registration_id: str | None
    boro: str | None
    house_number: str | None
    street_name: str | None
    zip_code: str | None
    apartment: str | None
    violation_class: str | None
    inspection_date: date | None
    approved_date: date | None
    certified_date: date | None
    order_number: str | None
    nov_id: str | None
    nov_description: str | None
    current_status: str | None
    current_status_date: date | None
    source_id: str
    source_version_id: str


class HpdViolationSearchResponse(BaseModel):
    count: int
    results: list[HpdViolationResponse]
