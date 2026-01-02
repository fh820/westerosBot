# app/services/common.py
from sqlalchemy import select
from app.db.models import Fief
import re


def slugify(text: str) -> str:
    """Standardizes strings to match Discord channel naming rules."""
    # 1. Lowercase
    # 2. Replace spaces with hyphens
    # 3. Remove any character that isn't a-z, 0-9, or a hyphen
    # 4. Collapse multiple hyphens into one
    text = text.lower().replace(" ", "-")
    text = re.sub(r"[^a-z0-9-]", "", text)
    return re.sub(r"-+", "-", text).strip("-")


async def get_location_from_db(session, game_id: int, name: str):
    """
    FAST lookup for Fief data from the database.
    Now a standalone function to avoid circular imports.
    """
    stmt = select(Fief).where(Fief.game_id == game_id, Fief.name.ilike(name))
    fief = (await session.execute(stmt)).scalars().first()
    if not fief:
        return None
    return {
        "x": fief.location_x,
        "y": fief.location_y,
        "castle": fief.name,
        "region": fief.region,
    }
