"""Approved resource contracts compatible with the Tumbleweed agent database."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PlaceKind = Literal["foodbank", "grocery", "market", "meal"]
Channel = Literal["imessage", "sms", "web"]
Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
Region = Literal["rural", "seattle"]


class GeoPoint(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]  # (lng, lat) -- longitude FIRST


class _MongoDoc(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class Place(_MongoDoc):
    id: str = Field(alias="_id")  # string slug, e.g. "hope-pantry-omak"
    name: str
    kind: PlaceKind
    address: str
    location: GeoPoint
    hours: str | None = None  # human text, e.g. "Tue/Thu 10am-2pm"
    days: list[Day] = Field(default_factory=list)  # structured open days; [] = unknown
    accepts_snap: bool = False
    phone: str | None = None
    notes: str | None = None  # curated facts: "No ID required, Spanish spoken"
    region: Region


class PlaceResult(Place):
    distance_mi: float


class FoodEvent(_MongoDoc):
    id: str = Field(alias="_id")
    title: str
    place_name: str
    place_id: str | None = Field(default=None, min_length=1)  # references places._id; optional for older records
    address: str
    location: GeoPoint
    starts_at: datetime  # stored as a BSON date (UTC)
    ends_at: datetime | None = None
    notes: str | None = None
    region: Region


class ZipDoc(_MongoDoc):
    id: str = Field(alias="_id")  # 5-digit ZIP
    city: str  # lowercase, e.g. "omak"
    state: str  # "WA"
    location: GeoPoint
