"""Exercise the actual deployment entrypoint with clean deployment environments."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "role,path,hidden",
    [
        ("public", "/directory", "/maintain"),
        ("moderator", "/maintain", "/directory"),
    ],
)
def test_vercel_entrypoint_routes_and_assets(role, path, hidden):
    code = f"""
from fastapi.testclient import TestClient
from index import app
with TestClient(app) as client:
    assert client.get('/').url.path == {path!r}
    assert client.get({path!r}).status_code == 200
    assert client.get({hidden!r}).status_code == 404
    assert client.get('/directory-assets/site.js').status_code == 200
    assert client.get('/directory-assets/site.css').status_code == 200
    assert client.get('/health').json() == {{'ok': True}}
"""
    env = {"PATH": os.environ.get("PATH", ""), "VERCEL": "1", "SERVICE_ROLE": role}
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, "Deployment entrypoint failed (subprocess output withheld)"


def test_vercel_entrypoint_requires_explicit_role():
    env = {"PATH": os.environ.get("PATH", ""), "VERCEL": "1"}
    result = subprocess.run([sys.executable, "-c", "import index"], cwd=ROOT, env=env, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert b"Set SERVICE_ROLE" in result.stderr
