"""Approved editing: authenticated boundaries, legacy data, conflicts and metadata preservation."""

import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from app.routes import resources
from test_directory import Collection, Database, client, sign_in  # noqa: F401


class ApprovedCollection(Collection):
    def match(self, row, query):
        for key, expected in query.items():
            if key == "$and":
                if not all(self.match(row, q) for q in expected):
                    return False
            elif key == "$or":
                if not any(self.match(row, q) for q in expected):
                    return False
            elif key == "$expr":
                if row != expected["$eq"][1]["$literal"]:
                    return False
            elif isinstance(expected, dict) and "$regex" in expected:
                flags = re.I if expected.get("$options") == "i" else 0
                if re.search(expected["$regex"], str(row.get(key, "")), flags) is None:
                    return False
            elif row.get(key) != expected:
                return False
        return True

    def find(self, query, projection=None):
        return super().find(query)


def place(identifier="legacy-pantry"):
    return {
        "_id": identifier,
        "name": "Legacy pantry",
        "kind": "foodbank",
        "address": "123 Main Street, Othello, WA 99344",
        "location": {"type": "Point", "coordinates": [-119.16, 46.82]},
        "region": "rural",
        "hours": "Tuesday 10 am",
        "source_checked_on": "2026-10-17",
        "provenance": {"provider": "county"},
    }


@pytest.fixture
def records(client, monkeypatch):  # noqa: F811 - imported pytest fixture
    http, _ = client
    db = Database()
    db.places, db.events = ApprovedCollection(), ApprovedCollection()
    db.places.rows["legacy-pantry"] = place()
    db.events.rows["e_123456789abc"] = {
        "_id": "e_123456789abc",
        "title": "Harvest food drive",
        "address": place()["address"],
        "location": place()["location"],
        "region": "rural",
        "place_id": "legacy-pantry",
        "place_name": "Legacy pantry",
        "starts_at": datetime.now(UTC) + timedelta(days=3),
        "ends_at": datetime.now(UTC) + timedelta(days=3, hours=2),
        "zip": "99344",
        "event_kind": "donation_drive",
        "timezone": "America/Los_Angeles",
        "approval_submission_id": "original-submission",
    }
    monkeypatch.setattr(resources, "get_approved_db", lambda: db)
    monkeypatch.setattr(resources, "get_publisher_db", lambda: db)
    return http, db, sign_in(http)


def get_edit(http, headers, kind="place", identifier="legacy-pantry"):
    response = http.get(f"/api/moderation/resources/{kind}/{identifier}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    return {"version": data["version"], "resource": data["resource"], "note": "Verified correction"}


def test_search_and_edit_require_authentication(records):
    http, _, _ = records
    for path in ("/api/moderation/resources", "/api/moderation/resources/place/legacy-pantry"):
        assert http.get(path).status_code == 401
    assert http.put("/api/moderation/resources/place/legacy-pantry", json={}).status_code == 401


def test_search_literal_text_zip_filter_and_event_type(records):
    http, db, headers = records
    for query in ("q=pantry", "zip=99344", "q=MAIN&zip=99344"):
        result = http.get("/api/moderation/resources?" + query, headers=headers)
        assert result.status_code == 200 and len(result.json()["items"]) == 1
    assert http.get("/api/moderation/resources?q=.*", headers=headers).json()["items"] == []
    assert http.get("/api/moderation/resources?zip=99118", headers=headers).json()["items"] == []
    result = http.get("/api/moderation/resources?resource_type=event&q=harvest", headers=headers)
    assert result.json()["items"][0]["_id"] == "e_123456789abc"
    for i in range(35):
        db.places.rows[str(i)] = place(str(i))
    first = http.get("/api/moderation/resources", headers=headers).json()
    second = http.get("/api/moderation/resources?offset=30", headers=headers).json()
    assert len(first["items"]) == 30 and first["has_more"]
    assert len(second["items"]) == 6 and not second["has_more"]
    assert not {r["_id"] for r in first["items"]} & {r["_id"] for r in second["items"]}


def test_legacy_place_edit_preserves_id_provenance_and_missing_fields(records):
    http, db, headers = records
    payload = get_edit(http, headers)
    payload["resource"]["hours"] = "Wednesday 1–3 pm"
    response = http.put("/api/moderation/resources/place/legacy-pantry", json=payload, headers=headers)
    assert response.status_code == 200
    row = db.places.rows["legacy-pantry"]
    assert row["hours"] == "Wednesday 1–3 pm" and row["_id"] == "legacy-pantry"
    assert row["provenance"] == {"provider": "county"}
    assert row["source_checked_on"] == "2026-10-17"
    assert "zip" not in row and "source_url" not in row
    assert row["employee_edits"][0]["note"] == "Verified correction"
    assert response.json()["version"] != payload["version"]
    assert http.put("/api/moderation/resources/place/legacy-pantry", json=payload, headers=headers).status_code == 409


def test_external_edits_and_race_cannot_be_overwritten(records, monkeypatch):
    http, db, headers = records
    payload = get_edit(http, headers)
    db.places.rows["legacy-pantry"]["external_note"] = "Concurrent import"
    path = "/api/moderation/resources/place/legacy-pantry"
    assert http.put(path, json=payload, headers=headers).status_code == 409
    payload = get_edit(http, headers)
    original = db.places.find_one_and_update

    async def racing_update(*args, **kwargs):
        db.places.rows["legacy-pantry"]["hours"] = "Changed during save"
        return await original(*args, **kwargs)

    monkeypatch.setattr(db.places, "find_one_and_update", racing_update)
    assert http.put(path, json=payload, headers=headers).status_code == 409
    assert db.places.rows["legacy-pantry"]["hours"] == "Changed during save"
    assert "employee_edits" not in db.places.rows["legacy-pantry"]


def test_event_edit_preserves_venue_dates_and_extra_fields(records):
    http, db, headers = records
    identifier = "e_123456789abc"
    original = deepcopy(db.events.rows[identifier])
    payload = get_edit(http, headers, "event", identifier)
    payload["resource"]["name"] = "Updated harvest food drive"
    response = http.put("/api/moderation/resources/event/" + identifier, json=payload, headers=headers)
    assert response.status_code == 200
    row = db.events.rows[identifier]
    assert row["title"] == "Updated harvest food drive"
    for key in ("place_id", "starts_at", "ends_at", "event_kind", "timezone", "approval_submission_id"):
        assert row[key] == original[key]
    assert row["_id"] == identifier and "name" not in row


def test_event_edit_rejects_missing_or_mismatched_venue(records):
    http, db, headers = records
    payload = get_edit(http, headers, "event", "e_123456789abc")
    payload["resource"]["place_id"] = "not-a-venue"
    path = "/api/moderation/resources/event/e_123456789abc"
    assert http.put(path, json=payload, headers=headers).status_code == 422
    payload["resource"]["place_id"] = "legacy-pantry"
    payload["resource"]["address"] = "A different address"
    assert http.put(path, json=payload, headers=headers).status_code == 422
    assert "employee_edits" not in db.events.rows["e_123456789abc"]


@pytest.mark.parametrize(
    "change",
    [
        {"lat": 100},
        {"lng": -200},
        {"zip": "bad"},
        {"source_url": "javascript:alert(1)"},
        {"_id": "overwrite"},
        {"name": " "},
        {"employee_edits": []},
    ],
)
def test_invalid_edit_is_rejected(records, change):
    http, db, headers = records
    payload = get_edit(http, headers)
    payload["resource"].update(change)
    assert http.put("/api/moderation/resources/place/legacy-pantry", json=payload, headers=headers).status_code == 422
    assert "employee_edits" not in db.places.rows["legacy-pantry"]


def test_type_changes_missing_records_and_query_bounds(records):
    http, _, headers = records
    payload = get_edit(http, headers, "event", "e_123456789abc")
    assert http.put("/api/moderation/resources/place/legacy-pantry", json=payload, headers=headers).status_code == 422
    assert http.get("/api/moderation/resources/place/missing", headers=headers).status_code == 404
    for query in ("resource_type=users", "zip=invalid", "offset=-1", "q=" + "x" * 101):
        assert http.get("/api/moderation/resources?" + query, headers=headers).status_code == 422


def test_approved_edit_requires_reason_and_valid_event_end(records):
    http, db, headers = records
    payload = get_edit(http, headers)
    payload["note"] = " "
    assert http.put("/api/moderation/resources/place/legacy-pantry", json=payload, headers=headers).status_code == 422
    payload = get_edit(http, headers, "event", "e_123456789abc")
    payload["resource"]["ends_at"] = payload["resource"]["starts_at"]
    assert http.put("/api/moderation/resources/event/e_123456789abc", json=payload, headers=headers).status_code == 422
    assert "employee_edits" not in db.events.rows["e_123456789abc"]
