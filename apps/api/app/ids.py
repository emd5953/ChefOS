import uuid


def new_id() -> uuid.UUID:
    """UUIDv7 primary key, generated in the app."""
    return uuid.uuid7()
