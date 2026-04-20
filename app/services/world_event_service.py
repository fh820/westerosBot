from sqlalchemy import or_, select

from app.db.models import Game, House, WorldEvent


class WorldEventService:
    """Small GM-only campaign timeline logger."""

    def __init__(self, session):
        self.session = session

    async def log(
        self,
        game_id: int,
        category: str,
        event_type: str,
        title: str,
        summary: str | None = None,
        *,
        actor_house_id: int | None = None,
        target_house_id: int | None = None,
        army_id: int | None = None,
        target_army_id: int | None = None,
        fief_id: int | None = None,
        battle_id: int | None = None,
        metadata: dict | None = None,
    ) -> WorldEvent:
        event = WorldEvent(
            game_id=game_id,
            category=(category or "system").lower(),
            event_type=(event_type or "note").lower(),
            title=title[:250],
            summary=summary,
            actor_house_id=actor_house_id,
            target_house_id=target_house_id,
            army_id=army_id,
            target_army_id=target_army_id,
            fief_id=fief_id,
            battle_id=battle_id,
            event_metadata=metadata or {},
        )
        self.session.add(event)
        return event

    async def recent(
        self,
        game_id: int,
        limit: int = 25,
        query: str | None = None,
    ) -> list[WorldEvent]:
        limit = max(1, min(int(limit or 25), 100))
        stmt = select(WorldEvent).where(WorldEvent.game_id == game_id)

        if query:
            q = query.strip()
            like = f"%{q}%"
            stmt = stmt.outerjoin(
                House,
                or_(
                    House.house_id == WorldEvent.actor_house_id,
                    House.house_id == WorldEvent.target_house_id,
                ),
            ).where(
                or_(
                    WorldEvent.category.ilike(q),
                    WorldEvent.event_type.ilike(q),
                    WorldEvent.title.ilike(like),
                    WorldEvent.summary.ilike(like),
                    House.name.ilike(like),
                )
            )

        stmt = stmt.order_by(WorldEvent.created_at.desc(), WorldEvent.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().unique().all())

    @staticmethod
    def format_event(event: WorldEvent) -> str:
        timestamp = event.created_at.strftime("%Y-%m-%d %H:%M") if event.created_at else "unknown"
        category = (event.category or "system").title()
        line = f"`{event.id}` **{timestamp}** | **{category}** | {event.title}"
        if event.summary:
            line += f"\n{event.summary}"
        return line
