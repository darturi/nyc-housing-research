from pydantic import BaseModel, field_validator


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("Invalid email address.")
        return normalized


class UserResponse(BaseModel):
    id: str
    email: str
    is_admin: bool
    is_active: bool


class CurrentUserResponse(UserResponse):
    pass

