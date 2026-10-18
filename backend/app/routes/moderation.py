"""Authenticated, revision-checked moderation with retryable publication."""

from datetime import UTC, datetime
from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db import get_approved_db, get_publisher_db, get_submissions_db
from app.directory_models import ResourceInput, ReviewDecision, ReviewEdit
from app.maintainer_auth import require_maintainer
from app.models import FoodEvent, GeoPoint, Place
from app.routes.directory import WEB

router = APIRouter()
admin = APIRouter(prefix="/api/moderation", dependencies=[Depends(require_maintainer)])


@router.get("/maintain", include_in_schema=False)
async def maintainer_page():
    return FileResponse(WEB / "maintain.html", headers={"Cache-Control": "no-store"})


@admin.get("/submissions")
async def queue(
    status: str = Query(default="pending", pattern="^(pending|approving|approved|rejected)$"),
    offset: int = Query(default=0, ge=0, le=10000),
):
    cursor = get_submissions_db().pending_resources.find({"status": status}).sort([("created_at", 1), ("_id", 1)])
    rows = await cursor.skip(offset).limit(31).to_list(31)
    return {"items": rows[:30], "has_more": len(rows) > 30}


@admin.put("/submissions/{submission_id}")
async def edit(submission_id: str, body: ReviewEdit):
    resource = body.resource.model_dump(mode="python")
    resource["source_url"] = str(body.resource.source_url)
    now = datetime.now(UTC)
    row = await get_submissions_db().pending_resources.find_one_and_update(
        {"_id": submission_id, "status": "pending", "revision": body.revision},
        {
            "$set": {"resource": resource, "updated_at": now},
            "$inc": {"revision": 1},
            "$push": {"history": {"action": "edited", "at": now}},
        },
        return_document=ReturnDocument.AFTER,
    )
    if row is None:
        raise HTTPException(409, "Submission changed or is no longer pending. Reload before editing.")
    return row


def final_document(submission_id: str, resource: ResourceInput):
    location = GeoPoint(coordinates=(resource.lng, resource.lat))
    common = {
        "id": ("e_" if resource.resource_type == "event" else "p_") + sha256(submission_id.encode()).hexdigest()[:12],
        "address": resource.address,
        "location": location,
        "region": resource.region,
        "notes": resource.notes,
    }
    if resource.resource_type == "place":
        model = Place(
            **common,
            name=resource.name,
            kind=resource.kind,
            hours=resource.hours,
            days=resource.days,
            accepts_snap=resource.accepts_snap,
            phone=resource.phone,
        )
        collection = "places"
    else:
        model = FoodEvent(
            **common,
            title=resource.name,
            place_name=resource.place_name,
            place_id=resource.place_id,
            starts_at=resource.starts_at,
            ends_at=resource.ends_at,
        )
        collection = "events"
    doc = model.model_dump(mode="python", by_alias=True)
    doc.update(source_url=str(resource.source_url), zip=resource.zip, approval_submission_id=submission_id)
    return collection, doc


@admin.post("/submissions/{submission_id}/approve")
async def approve(submission_id: str, body: ReviewDecision):
    db = get_submissions_db()
    row = await db.pending_resources.find_one({"_id": submission_id})
    if row is None:
        raise HTTPException(404, "Submission not found")
    if row["revision"] != body.revision:
        raise HTTPException(409, "Submission changed. Reload before approving.")
    if row["status"] == "approved":
        return {"status": "approved", "published_id": row["published_id"]}
    if row["status"] not in ("pending", "approving"):
        raise HTTPException(409, "Only pending submissions can be approved")
    resource = ResourceInput.model_validate(row["resource"])
    now = datetime.now(UTC)
    if resource.resource_type == "event" and resource.starts_at <= now and row["status"] == "pending":
        raise HTTPException(422, "Event is in the past. Edit its date or reject it.")
    if resource.resource_type == "event" and row["status"] == "pending":
        venue = await get_approved_db().places.find_one({"_id": resource.place_id})
        coordinates = [resource.lng, resource.lat]
        if (
            not venue
            or venue["name"] != resource.place_name
            or venue["address"] != resource.address
            or venue["region"] != resource.region
            or list(venue["location"]["coordinates"]) != coordinates
        ):
            raise HTTPException(
                422, "Event venue must match an approved location's reference, name, address, area and coordinates"
            )
    if row["status"] == "pending":
        # Freeze the reviewed revision before publishing; edits/rejections now fail.
        row = await db.pending_resources.find_one_and_update(
            {"_id": submission_id, "status": "pending", "revision": body.revision},
            {
                "$set": {"status": "approving", "review_note": body.note, "updated_at": now},
                "$push": {"history": {"action": "approval_started", "at": now}},
            },
            return_document=ReturnDocument.AFTER,
        )
        if row is None:
            raise HTTPException(409, "Submission changed. Reload before approving.")
    collection, doc = final_document(submission_id, resource)
    doc["approved_at"] = row["updated_at"]
    try:
        await get_publisher_db()[collection].insert_one(doc)
    except DuplicateKeyError:
        existing = await get_publisher_db()[collection].find_one({"_id": doc["_id"]})
        if existing is None or existing.get("approval_submission_id") != submission_id:
            raise HTTPException(409, "Published ID collision; no existing record was overwritten") from None
    # If interrupted between insert and completion, retry the frozen revision safely.
    await db.pending_resources.update_one(
        {"_id": submission_id, "status": "approving", "revision": body.revision},
        {
            "$set": {"status": "approved", "published_id": doc["_id"], "updated_at": datetime.now(UTC)},
            "$push": {"history": {"action": "approved", "at": datetime.now(UTC)}},
        },
    )
    return {"status": "approved", "published_id": doc["_id"]}


@admin.post("/submissions/{submission_id}/reject")
async def reject(submission_id: str, body: ReviewDecision):
    now = datetime.now(UTC)
    result = await get_submissions_db().pending_resources.update_one(
        {"_id": submission_id, "status": "pending", "revision": body.revision},
        {
            "$set": {"status": "rejected", "review_note": body.note, "updated_at": now},
            "$push": {"history": {"action": "rejected", "at": now}},
        },
    )
    if not result.matched_count:
        raise HTTPException(409, "Submission changed or is no longer pending. Reload before rejecting.")
    return {"status": "rejected"}


router.include_router(admin)
