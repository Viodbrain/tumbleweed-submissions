"""Validated community submissions; shared agent contracts remain unchanged."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.models import Day, PlaceKind, Region


class ResourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    resource_type: Literal["place", "event"]
    name: str = Field(min_length=2, max_length=160)
    address: str = Field(min_length=5, max_length=300)
    zip: str = Field(pattern=r"^[0-9]{5}$")
    lat: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    lng: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
    region: Region = "rural"
    kind: PlaceKind | None = None
    hours: str | None = Field(default=None, max_length=500)
    days: list[Day] = Field(default_factory=list, max_length=7)
    accepts_snap: bool = False
    phone: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)
    place_id: str | None = Field(default=None, min_length=1, max_length=100)
    ends_at: datetime | None = None
    place_name: str | None = Field(default=None, max_length=160)
    starts_at: datetime | None = None
    source_url: HttpUrl

    @field_validator("starts_at", "ends_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Event time must include a timezone")
        return value

    @model_validator(mode="after")
    def required_details(self):
        if self.resource_type == "place" and self.kind is None:
            raise ValueError("Choose a place type")
        if self.resource_type == "event" and (self.starts_at is None or not self.place_name or not self.place_id):
            raise ValueError("Events require an approved venue reference, venue name, and timezone-aware start time")
        if self.ends_at is not None and (self.starts_at is None or self.ends_at <= self.starts_at):
            raise ValueError("Event end must be later than its start")
        return self


class ReviewEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    resource: ResourceInput


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=1)
    note: str = Field(min_length=3, max_length=1000)


class PublicSubmission(ResourceInput):
    """Visitors can leave map verification to employees for a new location."""

    lat: Annotated[float | None, Field(ge=-90, le=90, allow_inf_nan=False)] = None
    lng: Annotated[float | None, Field(ge=-180, le=180, allow_inf_nan=False)] = None

    @model_validator(mode="after")
    def coordinate_pair(self):
        if (self.lat is None) != (self.lng is None):
            raise ValueError("Provide both map coordinates or leave both blank")
        return self
