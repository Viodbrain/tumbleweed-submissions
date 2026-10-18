"""Service capabilities and configuration fail closed without any Atlas connection."""

import pytest
from app import db
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_public_service_has_no_login_or_moderation_routes():
    with TestClient(create_app("public")) as client:
        assert client.get("/directory").status_code == 200
        assert client.get("/maintain").status_code == 404
        assert client.get("/api/moderation/submissions").status_code == 404
        assert client.post("/api/maintainer/login", json={}).status_code == 404


def test_moderator_service_has_no_public_submission_routes():
    with TestClient(create_app("moderator")) as client:
        assert client.get("/maintain").status_code == 200
        assert client.post("/api/submissions", json={}).status_code == 404
        assert client.get("/directory").status_code == 404


def test_database_names_must_be_separate():
    with pytest.raises(ValidationError, match="must be different"):
        Settings(submissions_db="tumbleweed", approved_db="tumbleweed")


def test_public_process_rejects_publisher_secret():
    with pytest.raises(ValidationError, match="must not receive"):
        Settings(service_role="public", approved_write_uri="mongodb://secret")


def test_public_process_cannot_request_writer(monkeypatch):
    monkeypatch.setattr(db, "get_config", lambda: Settings(service_role="public"))
    with pytest.raises(RuntimeError, match="Only the moderator"):
        db.get_publisher_db()


def test_missing_connection_never_falls_back_to_another_uri(monkeypatch):
    monkeypatch.setattr(db, "get_config", lambda: Settings(service_role="moderator"))
    for getter in (db.get_submissions_db, db.get_approved_db, db.get_publisher_db):
        with pytest.raises(RuntimeError, match="not configured"):
            getter()


def test_default_home_opens_maintainer_login():
    with TestClient(create_app()) as client:
        response = client.get("/")
        assert response.url.path == "/maintain"
        assert "Employee sign-in" in response.text
        assert client.get("/directory").status_code == 404


def test_login_reports_missing_configuration_without_exposing_secrets(monkeypatch):
    from app import maintainer_auth

    def unconfigured():
        raise RuntimeError("MongoDB connection for submissions is not configured")

    monkeypatch.setattr(maintainer_auth, "get_submissions_db", unconfigured)
    with TestClient(create_app("moderator")) as client:
        response = client.post("/api/maintainer/login", json={"username": "test", "password": "test"})
        assert response.status_code == 503
        assert (
            response.json()["detail"] == "Maintainer sign-in is not configured yet. Contact the project administrator."
        )
        assert client.get("/api/moderation/submissions").status_code == 401
