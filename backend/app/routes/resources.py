"""Authenticated search and optimistic editing of approved locations/events."""

import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from bson import json_util
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from pymongo import ReturnDocument

from app.db import get_approved_db, get_publisher_db
from app.directory_models import ResourceInput
from app.maintainer_auth import require_maintainer

router = APIRouter(prefix="/api/moderation/resources", dependencies=[Depends(require_maintainer)])
COLLECTIONS = {"place": "places", "event": "events"}
ResourceType = Literal["place", "event"]


class ApprovedResource(ResourceInput):
    # Older seeds do not always include these fields. Do not invent missing data.
    zip: str | None = Field(default=None, pattern=r"^[0-9]{5}$")
    source_url: HttpUrl | None = None

    @field_validator("zip", "source_url", mode="before")
    @classmethod
    def empty_optional(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class ApprovedEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str = Field(min_length=3, max_length=1000)
    # A full-document digest also detects edits from outside this application.
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource: ApprovedResource


def version(row):
    serialized = json_util.dumps(row, json_options=json_util.CANONICAL_JSON_OPTIONS, sort_keys=True)
    return sha256(serialized.encode()).hexdigest()


def editable(row, resource_type):
    coordinates = row["location"]["coordinates"]
    values = {key: row[key] for key in ResourceInput.model_fields if key in row}
    values.update(
        resource_type=resource_type,
        name=row.get("name") if resource_type == "place" else row.get("title"),
        lng=coordinates[0],
        lat=coordinates[1],
    )
    return values


def detail(row, resource_type):
    return {
        "id": row["_id"],
        "version": version(row),
        "resource": editable(row, resource_type),
        "history": row.get("employee_edits", [])[-10:],
    }


@router.get("")
async def search(
    resource_type: ResourceType = "place",
    q: str = Query(default="", max_length=100),
    zip: str | None = Query(default=None, pattern=r"^[0-9]{5}$"),
    offset: int = Query(default=0, ge=0, le=10000),
):
    filters = []
    if q.strip():
        pattern = {"$regex": re.escape(q.strip()), "$options": "i"}
        fields = ("name", "address", "_id") if resource_type == "place" else ("title", "place_name", "address", "_id")
        filters.append({"$or": [{key: pattern} for key in fields]})
    if zip:
        # Older records may store the postal code only in their street address.
        filters.append({"$or": [{"zip": zip}, {"address": {"$regex": rf"\b{zip}\b"}}]})
    query = {"$and": filters} if filters else {}
    name = "name" if resource_type == "place" else "title"
    projection = {key: 1 for key in ("_id", name, "address", "zip", "place_name", "starts_at", "hours")}
    collection = get_approved_db()[COLLECTIONS[resource_type]]
    rows = await collection.find(query, projection).sort([(name, 1), ("_id", 1)]).skip(offset).limit(31).to_list(31)
    return {"items": rows[:30], "has_more": len(rows) > 30}


@router.get("/{resource_type}/{identifier}")
async def get_resource(resource_type: ResourceType, identifier: str):
    row = await get_approved_db()[COLLECTIONS[resource_type]].find_one({"_id": identifier})
    if row is None:
        raise HTTPException(404, "Approved record not found")
    return detail(row, resource_type)


@router.put("/{resource_type}/{identifier}")
async def save(resource_type: ResourceType, identifier: str, body: ApprovedEdit):
    if body.resource.resource_type != resource_type:
        raise HTTPException(422, "An existing record cannot change between a location and an event")
    collection = get_publisher_db()[COLLECTIONS[resource_type]]
    original = await collection.find_one({"_id": identifier})
    if original is None:
        raise HTTPException(404, "Approved record not found")
    if version(original) != body.version:
        raise HTTPException(409, "This record changed. Reload it and reapply your edits before saving.")
    resource = body.resource
    if resource_type == "event":
        venue = await get_approved_db().places.find_one({"_id": resource.place_id})
        if (
            not venue
            or venue["name"] != resource.place_name
            or venue["address"] != resource.address
            or venue["region"] != resource.region
            or list(venue["location"]["coordinates"]) != [resource.lng, resource.lat]
        ):
            raise HTTPException(
                422, "Use the current approved venue name, address, area and coordinates for this event"
            )
    fields = resource.model_dump(mode="python")
    fields.pop("resource_type")
    fields.pop("lat")
    fields.pop("lng")
    fields["location"] = {"type": "Point", "coordinates": [resource.lng, resource.lat]}
    if fields.get("source_url") is not None:
        fields["source_url"] = str(resource.source_url)
    if resource_type == "event":
        fields["title"] = fields.pop("name")
        for key in ("kind", "hours", "days", "accepts_snap", "phone"):
            fields.pop(key)
    else:
        for key in ("place_id", "place_name", "starts_at", "ends_at"):
            fields.pop(key)
    # Avoid adding null optional fields to legacy records simply by opening/saving.
    fields = {key: value for key, value in fields.items() if value is not None or key in original}
    now = datetime.now(UTC)
    updated = await collection.find_one_and_update(
        {"_id": identifier, "$expr": {"$eq": ["$$ROOT", {"$literal": original}]}},
        {
            "$set": {**fields, "updated_at": now},
            "$push": {"employee_edits": {"at": now, "actor": "shared_employee", "note": body.note}},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "This record changed while saving. Reload it and reapply your edits.")
    return detail(updated, resource_type)
