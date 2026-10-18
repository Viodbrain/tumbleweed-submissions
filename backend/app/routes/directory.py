"""Public directory and moderated submissions. Never expose the pending queue publicly."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pymongo import ReturnDocument

from app import data
from app.db import get_approved_db, get_submissions_db
from app.directory_models import PublicSubmission

router = APIRouter()
WEB = Path(__file__).resolve().parents[1] / "web"


@router.get("/directory", include_in_schema=False)
async def directory_page():
    return FileResponse(WEB / "directory.html", headers={"Cache-Control": "no-store"})


async def throttle(request: Request, bucket: str, maximum: int):
    # Trust request.client only. Forwarded headers require a trusted reverse proxy configuration.
    now = datetime.now(UTC)
    hour = now.replace(minute=0, second=0, microsecond=0)
    peer = request.client.host if request.client else "unknown"
    identity = hashlib.sha256(peer.encode()).hexdigest()
    row = await get_submissions_db().directory_limits.find_one_and_update(
        {"_id": f"{bucket}:{identity}:{hour.isoformat()}"},
        {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": hour + timedelta(hours=2)}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if row["count"] > maximum:
        raise HTTPException(429, "Too many requests. Please try again next hour.", headers={"Retry-After": "3600"})


@router.get("/api/directory")
async def browse(
    request: Request,
    zip: str = Query(pattern=r"^[0-9]{5}$"),
    radius: float = Query(default=40, ge=1, le=100, allow_inf_nan=False),
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000),
):
    await throttle(request, "browse", 600)
    origin = await data.resolve_point(zip=zip)
    if origin is None:
        raise HTTPException(404, "This ZIP is not in our directory yet. You can still submit a resource.")
    db = get_approved_db()
    now = datetime.now(UTC)

    async def nearby(collection, query, sort):
        cursor = await collection.aggregate(
            [
                {
                    "$geoNear": {
                        "near": origin.location.model_dump(),
                        "key": "location",
                        "spherical": True,
                        "distanceField": "distance_mi",
                        "distanceMultiplier": 1 / data.M_PER_MI,
                        "maxDistance": radius * data.M_PER_MI,
                        "query": query,
                    }
                },
                {"$sort": sort},
                {"$skip": offset},
                {"$limit": limit + 1},
                {
                    "$project": {
                        "_id": 1,
                        "zip": 1,
                        "region": 1,
                        "name": 1,
                        "kind": 1,
                        "address": 1,
                        "location": 1,
                        "hours": 1,
                        "days": 1,
                        "accepts_snap": 1,
                        "phone": 1,
                        "notes": 1,
                        "title": 1,
                        "place_name": 1,
                        "place_id": 1,
                        "ends_at": 1,
                        "starts_at": 1,
                        "distance_mi": 1,
                        "source_url": 1,
                    }
                },
            ]
        )
        rows = [row async for row in cursor]
        return {"items": rows[:limit], "has_more": len(rows) > limit}

    return {
        "zip": zip,
        "radius_mi": radius,
        "places": await nearby(db.places, {}, {"distance_mi": 1, "_id": 1}),
        "events": await nearby(
            db.events, {"starts_at": {"$gte": now, "$lte": now + timedelta(days=days)}}, {"starts_at": 1, "_id": 1}
        ),
    }


@router.post("/api/submissions", status_code=202)
async def submit(resource: PublicSubmission, request: Request):
    await throttle(request, "submit", 5)
    now = datetime.now(UTC)
    if resource.resource_type == "event" and resource.starts_at <= now:
        raise HTTPException(422, "Events must start in the future")
    if resource.resource_type == "event":
        venue = await get_approved_db().places.find_one({"_id": resource.place_id})
        if venue is None:
            raise HTTPException(422, "This venue is no longer available. Choose another approved location.")
        postal_match = re.search(r"\bWA\s+(\d{5})(?:-\d{4})?\b", venue["address"], re.IGNORECASE)
        resource = resource.model_copy(
            update={
                "place_name": venue["name"],
                "address": venue["address"],
                "region": venue["region"],
                "lng": venue["location"]["coordinates"][0],
                "lat": venue["location"]["coordinates"][1],
                "zip": venue.get("zip") or (postal_match.group(1) if postal_match else resource.zip),
            }
        )
    submission_id = str(uuid4())
    doc = resource.model_dump(mode="python")
    doc["source_url"] = str(resource.source_url)
    await get_submissions_db().pending_resources.insert_one(
        {
            "_id": submission_id,
            "status": "pending",
            "revision": 1,
            "resource": doc,
            "created_at": now,
            "updated_at": now,
            "history": [{"action": "submitted", "at": now}],
        }
    )
    return {"submission_id": submission_id, "status": "pending"}


@router.get("/api/venues")
async def venues(request: Request, zip: str = Query(pattern=r"^[0-9]{5}$"), q: str = Query(default="", max_length=100)):
    await throttle(request, "venues", 300)
    filters = [{"$or": [{"zip": zip}, {"address": {"$regex": rf"\b{zip}\b"}}]}]
    if q.strip():
        filters.append({"name": {"$regex": re.escape(q.strip()), "$options": "i"}})
    projection = {key: 1 for key in ("_id", "name", "address", "zip", "location", "region")}
    rows = (
        await get_approved_db()
        .places.find({"$and": filters}, projection)
        .sort([("name", 1), ("_id", 1)])
        .limit(51)
        .to_list(51)
    )
    return {"items": rows[:50], "has_more": len(rows) > 50}
