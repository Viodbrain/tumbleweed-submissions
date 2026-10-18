"""One local .env; deployed process environment takes precedence."""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, SecretStr, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseModel):
    service_role: str = "moderator"
    submissions_db: str = "tumbleweed_submissions"
    approved_db: str = "tumbleweed"
    submissions_uri: SecretStr | None = None
    approved_read_uri: SecretStr | None = None
    approved_write_uri: SecretStr | None = None
    allowed_origins: str = ""

    @field_validator("service_role")
    @classmethod
    def role(cls, value):
        if value not in ("public", "moderator"):
            raise ValueError("SERVICE_ROLE must be public or moderator")
        return value

    @field_validator("submissions_db", "approved_db")
    @classmethod
    def database_name(cls, value):
        if not value or len(value.encode()) > 38 or any(c in value for c in '/\\. "$*<>:|?\x00'):
            raise ValueError("Use a valid Atlas database name of at most 38 bytes")
        return value

    @model_validator(mode="after")
    def boundaries(self):
        if self.submissions_db == self.approved_db:
            raise ValueError("Submissions and approved databases must be different")
        if self.service_role == "public" and self.approved_write_uri:
            raise ValueError("Public service must not receive APPROVED_WRITE_MONGODB_URI")
        return self


@lru_cache(maxsize=1)
def get_config():
    local = {} if os.getenv("VERCEL") else dotenv_values(ROOT / ".env", interpolate=False)
    env = {**local, **os.environ}
    mapping = {
        "SERVICE_ROLE": "service_role",
        "SUBMISSIONS_DB": "submissions_db",
        "APPROVED_DB": "approved_db",
        "SUBMISSIONS_MONGODB_URI": "submissions_uri",
        "APPROVED_READ_MONGODB_URI": "approved_read_uri",
        "APPROVED_WRITE_MONGODB_URI": "approved_write_uri",
        "ALLOWED_ORIGINS": "allowed_origins",
    }
    return Settings(**{field: env[key] for key, field in mapping.items() if env.get(key)})
