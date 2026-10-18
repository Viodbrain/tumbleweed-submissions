"""Create/rotate the shared login: python -m scripts.setup_maintainer."""
# ruff: noqa: I001

import asyncio
import getpass
import secrets
from datetime import UTC, datetime


from app.db import close_db, get_submissions_db
from app.maintainer_auth import PASSWORDS


async def save_credential(username, password):
    hashed = await asyncio.to_thread(PASSWORDS.hash, password)
    await get_submissions_db().maintainer_credentials.update_one(
        {"_id": "shared"},
        {
            "$set": {
                "username": username,
                "password_hash": hashed,
                "version": secrets.token_hex(16),
                "updated_at": datetime.now(UTC),
            }
        },
        upsert=True,
    )
    # Version change immediately invalidates every old session, even before this cleanup.
    await get_submissions_db().maintainer_sessions.delete_many({})


async def main():
    username = input("Shared username: ").strip()
    password = getpass.getpass("New password (16–256 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if not username or len(username) > 80 or not 16 <= len(password) <= 256 or password != confirmation:
        raise SystemExit("Invalid username, password length, or confirmation. Nothing changed.")
    try:
        await save_credential(username, password)
        print("Shared credential saved as a salted Argon2id hash. Previous sessions revoked.")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
