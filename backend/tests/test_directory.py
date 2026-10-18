"""Directory boundaries, moderation races, and credential/session lifecycle; no real Atlas."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app import maintainer_auth as auth
from app.directory_models import ResourceInput
from app.main import create_app
from app.routes import directory, moderation
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError


class Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def sort(self, keys):
        for key, direction in reversed(keys):
            self.rows.sort(key=lambda row: row[key], reverse=direction < 0)
        return self

    def skip(self, offset):
        self.rows = self.rows[offset:]
        return self

    def limit(self, limit):
        self.rows = self.rows[:limit]
        return self

    async def to_list(self, limit):
        return self.rows[:limit]

    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                yield row

        return iterate()


class Collection:
    def __init__(self):
        self.rows = {}
        self.pipelines = []

    def match(self, row, query):
        return all(row.get(k) == v for k, v in query.items())

    async def insert_one(self, row):
        if row["_id"] in self.rows:
            raise DuplicateKeyError("Duplicate")
        self.rows[row["_id"]] = deepcopy(row)

    async def find_one(self, query):
        return next((deepcopy(r) for r in self.rows.values() if self.match(r, query)), None)

    async def update_one(self, query, update, upsert=False):
        row = next((r for r in self.rows.values() if self.match(r, query)), None)
        if row is None:
            if not upsert:
                return SimpleNamespace(matched_count=0)
            row = deepcopy(query) | deepcopy(update.get("$setOnInsert", {}))
            self.rows[row["_id"]] = row
        row.update(deepcopy(update.get("$set", {})))
        for k, v in update.get("$inc", {}).items():
            row[k] = row.get(k, 0) + v
        for k, v in update.get("$push", {}).items():
            row.setdefault(k, []).append(deepcopy(v))
        return SimpleNamespace(matched_count=1)

    async def find_one_and_update(self, query, update, **kwargs):
        result = await self.update_one(query, update, kwargs.get("upsert", False))
        if not result.matched_count:
            return None
        return deepcopy(self.rows[query["_id"]])

    async def delete_one(self, query):
        self.rows.pop(query["_id"], None)

    def find(self, query):
        return Cursor([row for row in self.rows.values() if self.match(row, query)])

    async def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        # Publication isolation is real here; geographic calculations belong to MongoDB.
        rows = list(self.rows.values())
        query = pipeline[0]["$geoNear"]["query"]
        if "starts_at" in query:
            bounds = query["starts_at"]
            rows = [r for r in rows if bounds["$gte"] <= r["starts_at"] <= bounds["$lte"]]
        offset = next(s["$skip"] for s in pipeline if "$skip" in s)
        limit = next(s["$limit"] for s in pipeline if "$limit" in s)
        return Cursor(rows[offset : offset + limit])


class Database:
    def __init__(self):
        for name in (
            "places",
            "events",
            "pending_resources",
            "directory_limits",
            "maintainer_credentials",
            "maintainer_sessions",
        ):
            setattr(self, name, Collection())

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture
def client(monkeypatch):
    pending = Database()
    approved = Database()
    db = SimpleNamespace(
        **{
            name: getattr(pending, name)
            for name in ("pending_resources", "directory_limits", "maintainer_credentials", "maintainer_sessions")
        },
        places=approved.places,
        events=approved.events,
    )
    # Separate fakes: an incorrect database/collection choice cannot silently pass.
    for name in ("places", "events"):
        delattr(pending, name)
    for name in ("pending_resources", "directory_limits", "maintainer_credentials", "maintainer_sessions"):
        delattr(approved, name)
    for module in (directory, moderation, auth):
        monkeypatch.setattr(module, "get_submissions_db", lambda: pending)
    monkeypatch.setattr(directory, "get_approved_db", lambda: approved)
    monkeypatch.setattr(moderation, "get_publisher_db", lambda: approved)
    monkeypatch.setattr(moderation, "get_approved_db", lambda: approved)
    app = create_app("moderator")
    app.include_router(directory.router)
    monkeypatch.setattr(
        directory.data,
        "resolve_point",
        AsyncMock(
            return_value=SimpleNamespace(
                location=SimpleNamespace(model_dump=lambda: {"type": "Point", "coordinates": [-119.16, 46.82]}),
            )
        ),
    )
    db.maintainer_credentials.rows["shared"] = {
        "_id": "shared",
        "username": "maintainer",
        "password_hash": auth.PASSWORDS.hash("long-test-password"),
        "version": "version-1",
    }
    with TestClient(app) as http:
        yield http, db


def resource(event=False):
    result = {
        "resource_type": "place",
        "name": "Synthetic pantry",
        "address": "123 Example Street",
        "zip": "99344",
        "lat": 46.82,
        "lng": -119.16,
        "kind": "foodbank",
        "source_url": "https://example.org/food",
        "notes": "Verified public details",
    }
    if event:
        result.update(
            resource_type="event",
            kind=None,
            place_name="Example hall",
            place_id="example-hall",
            starts_at=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
        )
    return result


def sign_in(http):
    response = http.post("/api/maintainer/login", json={"username": "maintainer", "password": "long-test-password"})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def submit(http, event=False):
    response = http.post("/api/submissions", json=resource(event))
    assert response.status_code == 202
    return response.json()["submission_id"]


@pytest.mark.parametrize("event", [False, True])
def test_pending_edit_approve_isolation_and_idempotency(client, event):
    http, db = client
    identifier = submit(http, event)
    assert not db.places.rows and not db.events.rows
    if event:
        db.places.rows["example-hall"] = {
            "_id": "example-hall",
            "name": "Example hall",
            "address": "123 Example Street",
            "region": "rural",
            "location": {"type": "Point", "coordinates": [-119.16, 46.82]},
        }
    browse = http.get("/api/directory?zip=99344").json()
    assert browse["events"]["items"] == []
    assert len(browse["places"]["items"]) == int(event)
    assert http.get("/api/moderation/submissions").status_code == 401
    assert (
        http.post(
            f"/api/moderation/submissions/{identifier}/approve", json={"revision": 1, "note": "Checked"}
        ).status_code
        == 401
    )
    headers = sign_in(http)
    pending = http.get("/api/moderation/submissions", headers=headers)
    assert pending.status_code == 200 and len(pending.json()["items"]) == 1
    changed = resource(event) | {"name": "Reviewed resource"}
    path = f"/api/moderation/submissions/{identifier}"
    edited = http.put(path, headers=headers, json={"revision": 1, "resource": changed})
    assert edited.status_code == 200 and edited.json()["revision"] == 2
    assert http.post(path + "/approve", headers=headers, json={"revision": 1, "note": "Checked"}).status_code == 409
    decision = {"revision": 2, "note": "Verified against public source"}
    response = http.post(path + "/approve", headers=headers, json=decision)
    assert response.status_code == 200
    assert http.post(path + "/approve", headers=headers, json=decision).json() == response.json()
    collection = db.events if event else db.places
    assert len(collection.rows) == 1
    row = next(iter(collection.rows.values()))
    assert row["location"]["coordinates"] == (-119.16, 46.82)
    assert row["approval_submission_id"] == identifier
    assert (row["title"] if event else row["name"]) == "Reviewed resource"
    assert http.put(path, headers=headers, json={"revision": 2, "resource": changed}).status_code == 409
    assert http.post(path + "/reject", headers=headers, json=decision).status_code == 409
    results = http.get("/api/directory?zip=99344").json()
    assert len(results["events" if event else "places"]["items"]) == 1


def test_rejection_never_publishes(client):
    http, db = client
    identifier = submit(http)
    headers = sign_in(http)
    path = f"/api/moderation/submissions/{identifier}"
    decision = {"revision": 1, "note": "Source cannot be verified"}
    assert http.post(path + "/reject", headers=headers, json=decision).status_code == 200
    assert http.post(path + "/approve", headers=headers, json=decision).status_code == 409
    assert db.pending_resources.rows[identifier]["status"] == "rejected"
    assert not db.places.rows


def test_publication_retry_completes_without_duplicate(client):
    http, db = client
    identifier = submit(http)
    row = db.pending_resources.rows[identifier]
    row["status"] = "approving"
    row["review_note"] = "Verified"
    _, doc = moderation.final_document(identifier, ResourceInput.model_validate(row["resource"]))
    db.places.rows[doc["_id"]] = doc
    headers = sign_in(http)
    response = http.post(
        f"/api/moderation/submissions/{identifier}/approve",
        headers=headers,
        json={"revision": 1, "note": "Retry publication"},
    )
    assert response.status_code == 200
    assert len(db.places.rows) == 1 and row["status"] == "approved"


@pytest.mark.parametrize(
    "change",
    [
        {"lat": float("inf")},
        {"lng": -200},
        {"zip": "bad"},
        {"kind": None},
        {"source_url": "javascript:alert(1)"},
        {"name": " "},
        {"notes": "a" * 2001},
        {"status": "approved"},
        {"resource_type": "event", "starts_at": "2027-01-01T10:00:00", "place_name": "Hall"},
    ],
)
def test_input_validation_rejects_bad_data(change):
    with pytest.raises(ValueError):
        ResourceInput.model_validate(resource() | change)


def test_body_limit_and_throttle(client):
    http, db = client
    for _ in range(5):
        assert http.post("/api/submissions", json=resource()).status_code == 202
    assert http.post("/api/submissions", json=resource()).status_code == 429
    assert len(db.pending_resources.rows) == 5
    assert http.post("/api/submissions", content="x" * 17000).status_code == 413


def test_password_salt_and_session_hashes_expiry_rotation_logout(client):
    http, db = client
    a = auth.PASSWORDS.hash("same-test-password")
    b = auth.PASSWORDS.hash("same-test-password")
    assert a != b and a.startswith("$argon2id$") and auth.PASSWORDS.verify(a, "same-test-password")
    assert "m=19456,t=2,p=1" in a
    assert http.post("/api/maintainer/login", json={"username": "maintainer", "password": "wrong"}).status_code == 401
    assert (
        http.post("/api/maintainer/login", json={"username": "other", "password": "long-test-password"}).status_code
        == 401
    )
    headers = sign_in(http)
    token = headers["Authorization"].split()[1]
    assert token not in db.maintainer_sessions.rows and auth.token_hash(token) in db.maintainer_sessions.rows
    response = http.get("/api/moderation/submissions", headers=headers)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    db.maintainer_sessions.rows[auth.token_hash(token)]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    assert http.get("/api/moderation/submissions", headers=headers).status_code == 401
    headers = sign_in(http)
    db.maintainer_credentials.rows["shared"]["version"] = "rotated"
    assert http.get("/api/moderation/submissions", headers=headers).status_code == 401
    headers = sign_in(http)
    assert http.post("/api/maintainer/logout", headers=headers).status_code == 200
    assert http.get("/api/moderation/submissions", headers=headers).status_code == 401


def test_unknown_zip_and_query_bounds(client, monkeypatch):
    http, _ = client
    monkeypatch.setattr(directory.data, "resolve_point", AsyncMock(return_value=None))
    assert http.get("/api/directory?zip=00000").status_code == 404
    assert http.get("/api/directory?zip=99344&radius=101").status_code == 422
    assert http.get("/api/directory?zip=99344&limit=99999").status_code == 422
    assert http.get("/api/directory?zip=99344&days=0").status_code == 422


def test_web_interfaces_available_without_exposing_credentials(client):
    http, _ = client
    assert http.get("/directory").status_code == 200
    assert http.get("/maintain").status_code == 200
    js = http.get("/directory-assets/site.js")
    assert js.status_code == 200
    assert "localStorage" not in js.text and "sessionStorage" not in js.text


def test_event_cannot_publish_with_missing_or_mismatched_venue(client):
    http, db = client
    identifier = submit(http, event=True)
    headers = sign_in(http)
    response = http.post(
        f"/api/moderation/submissions/{identifier}/approve",
        headers=headers,
        json={"revision": 1, "note": "Checked source"},
    )
    assert response.status_code == 422
    assert db.pending_resources.rows[identifier]["status"] == "pending"
    assert not db.events.rows


def test_new_events_require_an_explicit_venue_reference():
    body = resource(event=True)
    body.pop("place_id")
    with pytest.raises(ValueError, match="approved venue reference"):
        ResourceInput.model_validate(body)


def test_publication_uses_compact_event_ids_and_venue_links():
    body = ResourceInput.model_validate(resource(event=True))
    _, doc = moderation.final_document("submission-123", body)
    assert len(doc["_id"]) == 14 and doc["_id"].startswith("e_")
    assert doc["title"] == body.name and doc["place_id"] == body.place_id
