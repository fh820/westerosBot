from app.celery_app import celery_app
from app.db.sync_db import get_sync_session
from app.db.models import Game, House, Army
import redis
import json
import os

# --- Configuration ---
REDIS_CLIENT = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
# Cost per 100 soldiers per day
COST_PER_100 = 10


@celery_app.task
def daily_upkeep_tick():
    """
    1. Triggers every 24 hours via Celery Beat.
    2. Finds all active games where upkeep is ENABLED.
    3. Spawns a sub-task for each of those games (Scalability Pattern).
    """
    print("⏰ Daily Upkeep Tick Started...")
    session = get_sync_session()
    try:
        # <--- CHANGE: Now filters for both is_active and the new upkeep_enabled flag.
        games_to_process = (
            session.query(Game).filter_by(is_active=True, upkeep_enabled=True).all()
        )

        if not games_to_process:
            print("No active games with upkeep enabled. Task complete.")
            return

        print(
            f"Found {len(games_to_process)} active game(s) with upkeep enabled. Dispatching tasks..."
        )
        for game in games_to_process:
            # Spawn sub-task for each game that needs upkeep
            process_game_upkeep.delay(game.game_id)

    finally:
        session.close()


@celery_app.task
def process_game_upkeep(game_id: int):
    """
    Calculates and deducts upkeep for all houses in a single game.
    Reports any bankruptcies to the Game Master via Redis.
    """
    print(f"Processing upkeep for game_id: {game_id}...")
    session = get_sync_session()
    try:
        # <--- CORRECTION: Fetch the game object first for safety and to get guild_id.
        game = session.query(Game).get(game_id)
        if not game:
            print(f"❌ Could not find game with id {game_id}. Aborting task.")
            return

        # 1. Get all houses in this game
        houses = session.query(House).filter(House.game_id == game_id).all()
        if not houses:
            print(f"No houses found for game_id: {game_id}. Nothing to process.")
            return

        bankruptcies = []

        for house in houses:
            # 2. Find all non-garrisoned armies for the house to calculate costs
            field_armies = (
                session.query(Army)
                .filter(Army.house_id == house.house_id, Army.status != "GARRISONED")
                .all()
            )

            total_troops = sum(a.troop_count for a in field_armies)
            if total_troops <= 0:
                continue  # No troops, no cost.

            # 3. Calculate and deduct the cost
            cost = int((total_troops / 100) * COST_PER_100)

            print(
                f"  - House {house.name}: {total_troops} troops -> Cost: {cost}, Treasury: {house.treasury} -> {house.treasury - cost}"
            )

            house.treasury -= cost

            # 4. Check for bankruptcy
            if house.treasury < 0:
                print(
                    f"  - ⚠️  BANKRUPTCY: House {house.name} is now in debt by {abs(house.treasury)}."
                )
                bankruptcies.append(
                    {
                        "name": house.name,
                        "debt": abs(house.treasury),  # Report debt as a positive number
                        "troops": total_troops,
                    }
                )

        # Commit all treasury changes for the game in one transaction
        session.commit()

        # 5. Report bankruptcies to the GM via Redis, if any occurred
        if bankruptcies:
            print(f"Reporting {len(bankruptcies)} bankruptcies for game_id: {game_id}")
            payload = {
                "type": "BANKRUPTCY_ALERT",
                "guild_id": game.guild_id,  # <--- CORRECTION: Safely get guild_id from the game object
                "data": bankruptcies,
            }
            REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

    except Exception as e:
        print(
            f"❌ An unexpected error occurred in process_game_upkeep for game_id {game_id}: {e}"
        )
        session.rollback()
    finally:
        session.close()
        print(f"Finished upkeep processing for game_id: {game_id}.")
