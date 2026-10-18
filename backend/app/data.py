"""Public geographic lookup reads approved ZIPs only."""

from types import SimpleNamespace

from app.db import get_approved_db
from app.models import GeoPoint

M_PER_MI = 1609.344


async def resolve_point(zip):
    row = await get_approved_db().zips.find_one({"_id": zip})
    if row is None:
        return None
    return SimpleNamespace(location=GeoPoint.model_validate(row["location"]))
