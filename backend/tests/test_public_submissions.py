"""Visitors submit pending resources without gaining publishing or review access."""

from datetime import UTC, datetime, timedelta

import pytest
from app.routes import directory
from test_directory import Cursor, client, resource, sign_in  # noqa: F401
from test_resources import ApprovedCollection


def test_location_without_coordinates_waits_for_employee_verification(client):  # noqa: F811
    http, db = client
    payload = resource()
    payload.pop("lat")
    payload.pop("lng")
    response = http.post("/api/submissions", json=payload)
    assert response.status_code == 202
    identifier = response.json()["submission_id"]
    assert db.pending_resources.rows[identifier]["resource"]["lat"] is None
    assert not db.places.rows
    headers = sign_in(http)
    path = "/api/moderation/submissions/" + identifier
    response = http.post(path + "/approve", headers=headers, json={"revision": 1, "note": "Reviewed"})
    assert response.status_code == 422
    assert db.pending_resources.rows[identifier]["status"] == "pending"
    payload.update(lat=46.82, lng=-119.16)
    response = http.put(path, headers=headers, json={"revision": 1, "resource": payload})
    assert response.status_code == 200
    response = http.post(path + "/approve", headers=headers, json={"revision": 2, "note": "Coordinates verified"})
    assert response.status_code == 200 and len(db.places.rows) == 1


def test_public_event_uses_canonical_approved_venue_snapshot(client):  # noqa: F811
    http, db = client
    db.places.rows["example-hall"] = {
        "_id": "example-hall",
        "name": "Approved hall",
        "address": "456 Real Street, Othello, WA 99344",
        "zip": "99344",
        "region": "rural",
        "location": {"type": "Point", "coordinates": [-119.17, 46.83]},
    }
    response = http.post("/api/submissions", json=resource(event=True) | {"lat": None, "lng": None})
    assert response.status_code == 202
    row = db.pending_resources.rows[response.json()["submission_id"]]["resource"]
    assert row["place_name"] == "Approved hall" and row["address"] == db.places.rows["example-hall"]["address"]
    assert row["lat"] == 46.83 and row["lng"] == -119.17
    assert not db.events.rows


@pytest.mark.parametrize(
    "change",
    [
        {"lat": None},
        {"lat": 100},
        {"source_url": "javascript:bad"},
        {
            "resource_type": "event",
            "place_id": "missing",
            "place_name": "Missing hall",
            "starts_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
        {
            "resource_type": "event",
            "place_id": "missing",
            "place_name": "Missing hall",
            "starts_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    ],
)
def test_public_submission_validation(client, change):  # noqa: F811
    http, db = client
    assert http.post("/api/submissions", json=resource() | change).status_code == 422
    assert not db.pending_resources.rows


def test_public_venue_lookup_is_filtered_and_excludes_private_fields(client, monkeypatch):  # noqa: F811
    http, db = client

    class VenueCollection(ApprovedCollection):
        def find(self, query, projection=None):
            rows = super().find(query).rows
            if projection:
                rows = [{key: value for key, value in row.items() if projection.get(key)} for row in rows]
            return Cursor(rows)

    venues = VenueCollection()
    venues.rows["hall"] = {
        "_id": "hall",
        "name": "Community hall",
        "address": "123 Main, WA 99344",
        "location": {"type": "Point", "coordinates": [-119.16, 46.82]},
        "region": "rural",
        "employee_edits": [{"note": "Private review note"}],
    }
    venues.rows["other"] = dict(venues.rows["hall"], _id="other", name="Other hall", address="123 Main, WA 99118")
    db.places = venues

    class Reader:
        places = venues

    monkeypatch.setattr(directory, "get_approved_db", lambda: Reader())
    result = http.get("/api/venues?zip=99344&q=Community")
    assert result.status_code == 200 and len(result.json()["items"]) == 1
    assert result.json()["items"][0]["_id"] == "hall"
    assert "employee_edits" not in result.json()["items"][0]
    assert http.get("/api/venues?zip=99344&q=.*").json()["items"] == []
    assert http.get("/api/venues?zip=bad").status_code == 422
    assert http.get("/api/moderation/submissions").status_code == 401
