from app.celery_app import celery_app
from app.db.sync_db import get_sync_session
from app.db.models import Game, House, Army
import redis
import json
import os

REDIS_CLIENT = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

# CONFIG: Cost per 100 men per day
COST_PER_100 = 10


@celery_app.task
def daily_upkeep_tick():
    """
    1. Triggers every 24 hours via Celery Beat.
    2. Spawns a sub-task for every active game (Scalability Pattern).
    """
    print("⏰ Daily Upkeep Tick Started...")
    session = get_sync_session()
    games = session.query(Game).filter(Game.is_active == True).all()

    for game in games:
        # Spawn sub-task
        process_game_upkeep.delay(game.game_id)

    session.close()


@celery_app.task
def process_game_upkeep(game_id: int):
    """
    Calculates upkeep for a single game.
    """
    session = get_sync_session()
    try:
        # 1. Get all houses in this game
        houses = session.query(House).filter(House.game_id == game_id).all()

        bankruptcies = []

        for house in houses:
            # 2. Find FIELD Armies (Garrisons are free)
            # Note: Fleets count as field armies in this logic? Up to you.
            field_armies = (
                session.query(Army)
                .filter(Army.house_id == house.house_id, Army.status != "GARRISONED")
                .all()
            )

            total_troops = sum(a.troop_count for a in field_armies)

            if total_troops > 0:
                cost = int((total_troops / 100) * COST_PER_100)
                house.treasury -= cost

                # 3. Check Bankruptcy
                if house.treasury < 0:
                    bankruptcies.append(
                        {
                            "name": house.name,
                            "debt": house.treasury,
                            "troops": total_troops,
                        }
                    )

        session.commit()

        # 4. Report Bankruptcies to GM
        if bankruptcies:
            payload = {
                "type": "BANKRUPTCY_ALERT",
                "guild_id": houses[0].game.guild_id,  # Link back to Discord Server
                "data": bankruptcies,
            }
            REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

    except Exception as e:
        print(f"❌ Upkeep Error Game {game_id}: {e}")
        session.rollback()
    finally:
        session.close()
