# app/services/common.py
from sqlalchemy import select
from app.db.models import Fief


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
