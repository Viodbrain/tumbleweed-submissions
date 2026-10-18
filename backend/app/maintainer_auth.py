"""Shared maintainer credential: Argon2id password hash and opaque, hashed sessions."""

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.db import get_submissions_db
from app.routes.directory import throttle

# OWASP Argon2id minimum: 19 MiB, two iterations, one lane. Random salt per hash.
PASSWORDS = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
HASH_SLOTS = asyncio.Semaphore(2)
bearer = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api/maintainer")


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/login")
async def login(body: Login, request: Request):
    try:
        db = get_submissions_db()
    except RuntimeError:
        raise HTTPException(
            503, "Maintainer sign-in is not configured yet. Contact the project administrator."
        ) from None
    await throttle(request, "login", 20)
    account = await db.maintainer_credentials.find_one({"_id": "shared"})
    stored = account["password_hash"] if account else DUMMY_HASH
    try:
        async with HASH_SLOTS:
            await asyncio.to_thread(PASSWORDS.verify, stored, body.password)
    except (VerificationError, InvalidHashError):
        raise HTTPException(401, "Incorrect username or password") from None
    if not account or not secrets.compare_digest(body.username.encode(), account["username"].encode()):
        raise HTTPException(401, "Incorrect username or password")
    if PASSWORDS.check_needs_rehash(stored):
        async with HASH_SLOTS:
            updated = await asyncio.to_thread(PASSWORDS.hash, body.password)
        await db.maintainer_credentials.update_one(
            {"_id": "shared", "password_hash": stored},
            {"$set": {"password_hash": updated}},
        )
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=2)
    await db.maintainer_sessions.insert_one(
        {
            "_id": token_hash(token),
            "credential_version": account["version"],
            "expires_at": expires,
        }
    )
    return {"access_token": token, "token_type": "bearer", "expires_at": expires}


async def require_maintainer(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
):
    if credentials is None or len(credentials.credentials) > 128:
        raise HTTPException(401, "Sign in to continue", headers={"WWW-Authenticate": "Bearer"})
    await throttle(request, "admin", 300)
    db = get_submissions_db()
    session = await db.maintainer_sessions.find_one({"_id": token_hash(credentials.credentials)})
    account = await db.maintainer_credentials.find_one({"_id": "shared"})
    if (
        not session
        or not account
        or session["expires_at"] <= datetime.now(UTC)
        or session["credential_version"] != account["version"]
    ):
        raise HTTPException(401, "Your session has expired. Sign in again.", headers={"WWW-Authenticate": "Bearer"})
    return credentials.credentials


@router.post("/logout")
async def logout(token: Annotated[str, Depends(require_maintainer)]):
    await get_submissions_db().maintainer_sessions.delete_one({"_id": token_hash(token)})
    return {"signed_out": True}
