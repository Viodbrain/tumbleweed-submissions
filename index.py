"""Vercel ASGI entrypoint, shared by separate public and employee projects."""

import os
import sys
from pathlib import Path

if os.getenv("VERCEL") and os.getenv("SERVICE_ROLE") not in ("public", "moderator"):
    raise RuntimeError("Set SERVICE_ROLE to public or moderator in this Vercel project")

# Keep the existing backend package layout for both local Uvicorn and Vercel.
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))
from app.main import app  # noqa: E402, F401
