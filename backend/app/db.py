"""Explicit database capabilities; public process never has an approved writer."""

from pymongo import AsyncMongoClient

from app.config import get_config

_clients = {}


def database(capability, secret, name):
    if secret is None:
        raise RuntimeError(f"MongoDB connection for {capability} is not configured")
    if capability not in _clients:
        _clients[capability] = AsyncMongoClient(
            secret.get_secret_value(),
            tz_aware=True,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
        )
    return _clients[capability][name]


def get_submissions_db():
    config = get_config()
    return database("submissions", config.submissions_uri, config.submissions_db)


def get_approved_db():
    config = get_config()
    return database("approved reader", config.approved_read_uri, config.approved_db)


def get_publisher_db():
    config = get_config()
    if config.service_role != "moderator":
        raise RuntimeError("Only the moderator service can publish")
    return database("approved publisher", config.approved_write_uri, config.approved_db)


async def close_db():
    clients = list(_clients.values())
    _clients.clear()
    for client in clients:
        await client.close()
