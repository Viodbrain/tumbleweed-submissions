"""Run public and moderator interfaces as separate services from the same source."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import maintainer_auth
from app.config import get_config
from app.db import close_db
from app.directory_security import DirectorySecurity
from app.routes import directory, moderation


@asynccontextmanager
async def lifespan(app):
    yield
    await close_db()


def create_app(role=None):
    config = get_config()
    role = role or config.service_role
    if role not in ("public", "moderator"):
        raise ValueError("Unknown service role")
    app = FastAPI(title="Tumbleweed community resources", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in config.allowed_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
    )
    if role == "public":
        app.include_router(directory.router)
    else:
        app.include_router(moderation.router)
        app.include_router(maintainer_auth.router)

    @app.get("/", include_in_schema=False)
    async def home():
        return RedirectResponse("/directory" if role == "public" else "/maintain")

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.mount("/directory-assets", StaticFiles(directory=directory.WEB), name="directory-assets")
    app.add_middleware(DirectorySecurity)
    return app


app = create_app()
