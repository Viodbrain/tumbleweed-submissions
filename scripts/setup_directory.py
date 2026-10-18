"""Prepare moderation collections: python -m scripts.setup_directory --apply."""
# ruff: noqa: I001

import argparse
import asyncio

from pymongo.errors import CollectionInvalid


from app.db import close_db, get_submissions_db

INDEXES = {
    "pending_resources": [([("status", 1), ("created_at", 1), ("_id", 1)], {})],
    "maintainer_sessions": [([("expires_at", 1)], {"expireAfterSeconds": 0})],
    "directory_limits": [([("expires_at", 1)], {"expireAfterSeconds": 0})],
}


async def prepare(db):
    for collection, indexes in INDEXES.items():
        for keys, options in indexes:
            name = await db[collection].create_index(keys, **options)
            print(f"Ready: {collection}.{name}")
    try:
        await db.create_collection("maintainer_credentials")
    except CollectionInvalid:
        pass
    print("Ready: maintainer_credentials. Set its shared password with scripts.setup_maintainer.")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("Prepare pending queue, expiring request counters and sessions, and credential collection.")
        print("Dry run. Pass --apply to prepare Atlas. Existing resource and credential records are preserved.")
        return
    try:
        await prepare(get_submissions_db())
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
