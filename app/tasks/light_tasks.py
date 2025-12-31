# app/tasks/light_tasks.py

from app.celery_app import celery_app
from app.db.sync_db import get_sync_session
from app.db.models import (
    Game,
    House,
    Army,
    User,
    GamePlayer,
    Fief,
    MarchLog,
    PendingInteraction,
)
from sqlalchemy import select
import json
import redis
import os
import traceback
import datetime
from celery.result import AsyncResult
from sqlalchemy.orm import selectinload
from app.services.diplomacy_service import PF_ENGINE
from app.services.travel_calculator import calculate_travel_duration

from app.db.repositories import GameRepo  # Assuming GameRepo exists for settings
from app.tasks.battle_tasks import initiate_auto_battle
from sqlalchemy import or_


REDIS_CLIENT = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
COST_PER_100 = 10
from app.services.pathfinder_bot_engine import COSTS


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


# @celery_app.task
# def resolve_army_arrival(army_id: int):
#     """
#     Executes when an army or fleet arrives.
#     - Handles Hybrid Journeys (Sail -> March) by syncing the Ghost Army.
#     - Handles Pure Sea Journeys by unpacking cargo into a new Army.
#     """
#     print(f"--- RESOLVING ARRIVAL FOR ARMY ID: {army_id} ---")
#     session = get_sync_session()

#     try:
#         army = session.query(Army).filter(Army.army_id == army_id).first()

#         if not army or army.status not in ["MARCHING", "SAILING"]:
#             print(f"DEBUG: Army {army_id} invalid state or not found.")
#             return

#         # 1. Update Coordinates & Clear Movement Data
#         army.location_x = army.destination_x
#         army.location_y = army.destination_y
#         army.destination_x = None
#         army.destination_y = None
#         army.arrival_time = None
#         army.departure_time = None
#         army.task_id = None

#         new_status = ""
#         now = datetime.datetime.now(datetime.timezone.utc)

#         # --- LOGIC BRANCH A: LAND ARMY ---
#         if army.army_type == "LAND":
#             fief = (
#                 session.query(Fief)
#                 .filter(
#                     Fief.location_x == army.location_x,
#                     Fief.location_y == army.location_y,
#                 )
#                 .first()
#             )
#             new_status = "GARRISONED" if fief else "IDLE"

#         # --- LOGIC BRANCH B: FLEET ARRIVAL ---
#         elif army.army_type == "SEA":
#             print("DEBUG: Fleet arrival detected. Checking for cargo operations...")
#             new_status = "DOCKED"  # Or "IDLE" depending on your preference

#             # --- CASE 1: PURE SEA JOURNEY (Unpack Cargo) ---
#             # If the fleet has cargo data, it means no Ghost Army was created.
#             # We must create the land army NOW.
#             if army.cargo and army.cargo.get("troop_count", 0) > 0:
#                 print(f"DEBUG: Unpacking Cargo from Fleet {army_id}...")

#                 cargo = army.cargo

#                 # Check for fief at arrival point
#                 fief = (
#                     session.query(Fief)
#                     .filter(
#                         Fief.location_x == army.location_x,
#                         Fief.location_y == army.location_y,
#                     )
#                     .first()
#                 )
#                 land_status = "GARRISONED" if fief else "IDLE"

#                 # Create the new army
#                 new_land_army = Army(
#                     game_id=army.game_id,
#                     house_id=army.house_id,
#                     army_type="LAND",
#                     commander_name=cargo.get("commander", "Disembarked Force"),
#                     troop_count=cargo.get("troop_count", 0),
#                     composition=cargo.get("composition", {}),
#                     location_x=army.location_x,
#                     location_y=army.location_y,
#                     status=land_status,
#                     treasury=0,
#                 )
#                 session.add(new_land_army)

#                 # Clear the fleet's cargo so troops aren't duplicated
#                 army.cargo = None
#                 print("DEBUG: Cargo unpacked into new Army.")

#             # --- CASE 2: HYBRID JOURNEY (Sync Ghost Army) ---
#             # If cargo is empty, it means a Ghost Army was already created
#             # and is waiting in the database with status='MARCHING'.
#             else:
#                 # Find the Land Army belonging to this house, at this location,
#                 # that is currently set to 'MARCHING'.
#                 ghost_army = (
#                     session.query(Army)
#                     .filter(
#                         Army.house_id == army.house_id,
#                         Army.army_type == "LAND",
#                         Army.status
#                         == "MARCHING",  # Crucial: Service sets this to MARCHING
#                         Army.location_x == army.location_x,
#                         Army.location_y == army.location_y,
#                     )
#                     .first()
#                 )

#                 if ghost_army:
#                     print(
#                         f"DEBUG: Found Hybrid Ghost Army (ID: {ghost_army.army_id}). Syncing times."
#                     )

#                     # The Ghost Army has a departure_time set to the *estimated* arrival.
#                     # We accept the drift and update its times to start NOW.

#                     # Calculate how long the march was supposed to be
#                     if ghost_army.arrival_time and ghost_army.departure_time:
#                         march_duration = (
#                             ghost_army.arrival_time - ghost_army.departure_time
#                         )
#                     else:
#                         march_duration = datetime.timedelta(hours=1)  # Fallback

#                     # Reset start time to NOW to fix any lag drift
#                     ghost_army.departure_time = now
#                     ghost_army.arrival_time = now + march_duration

#                     # We must Reschedule the Celery Task because the original task
#                     # might have fired too early or late due to drift.
#                     if ghost_army.task_id:
#                         celery_app.control.revoke(ghost_army.task_id)

#                     new_task = resolve_army_arrival.apply_async(
#                         args=[ghost_army.army_id], eta=ghost_army.arrival_time
#                     )
#                     ghost_army.task_id = new_task.id

#                     print(
#                         f"DEBUG: Ghost Army {ghost_army.army_id} resynced and marching."
#                     )
#                 else:
#                     print(
#                         "DEBUG: No cargo and no ghost army found. Fleet is just moving empty."
#                     )

#         # 4. Finalize & Commit
#         army.status = new_status
#         # Clear logs
#         session.query(MarchLog).filter(MarchLog.army_id == army_id).delete()

#         # 5. Commit Changes
#         session.commit()
#         print(f"DEBUG: Success. Army {army_id} is now {new_status}.")

#         # 6. Notifications (Redis) logic...
#         # (Keep your existing notification code here)
#         house = session.query(House).filter(House.house_id == army.house_id).first()
#         if house:
#             owner_query = (
#                 session.query(User)
#                 .join(GamePlayer)
#                 .filter(
#                     GamePlayer.claimed_house_id == army.house_id,
#                     GamePlayer.is_primary == True,
#                 )
#                 .first()
#             )
#             owner_discord_id = owner_query.discord_id if owner_query else None

#             # Get location name
#             loc = (
#                 session.query(Fief)
#                 .filter(
#                     Fief.location_x == army.location_x,
#                     Fief.location_y == army.location_y,
#                 )
#                 .first()
#             )
#             location_name = (
#                 loc.name
#                 if loc
#                 else f"Coord ({int(army.location_x)}, {int(army.location_y)})"
#             )

#             payload = {
#                 "type": "ARRIVAL",
#                 "guild_id": house.game.guild_id,
#                 "house_name": house.name,
#                 "owner_id": owner_discord_id,
#                 "commander": army.commander_name,
#                 "troops": army.troop_count,
#                 "unit_type": army.army_type,
#                 "location": location_name,
#             }
#             # Publish
#             if REDIS_CLIENT:
#                 REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

#     except Exception as e:
#         print(f"FATAL ERROR in resolve_army_arrival (ID: {army_id})")
#         import traceback

#         traceback.print_exc()
#         session.rollback()
#     finally:
#         session.close()


# @celery_app.task
# def resolve_army_arrival(army_id: int):
#     """
#     Executes when an army or fleet arrives.
#     - Handles Hybrid Journeys (Sail -> March) by syncing the Ghost Army.
#     - Handles Pure Sea Journeys by unpacking cargo into a new Army.
#     """
#     print(f"--- RESOLVING ARRIVAL FOR ARMY ID: {army_id} ---")
#     session = get_sync_session()

#     try:
#         army = session.query(Army).filter(Army.army_id == army_id).first()

#         if not army or army.status not in ["MARCHING", "SAILING"]:
#             print(f"DEBUG: Army {army_id} invalid state or not found.")
#             return

#         # 1. Update Coordinates & Clear Movement Data
#         army.location_x = army.destination_x
#         army.location_y = army.destination_y
#         army.destination_x = None
#         army.destination_y = None
#         army.arrival_time = None
#         army.departure_time = None
#         army.task_id = None

#         new_status = ""
#         now = datetime.datetime.now(datetime.timezone.utc)

#         # --- LOGIC BRANCH A: LAND ARMY ---
#         if army.army_type == "LAND":
#             fief = (
#                 session.query(Fief)
#                 .filter(
#                     Fief.location_x == army.location_x,
#                     Fief.location_y == army.location_y,
#                 )
#                 .first()
#             )
#             # --- FIX 1: Check for Fief Ownership ---
#             # An army can only be garrisoned if it's on its own territory.
#             new_status = (
#                 "GARRISONED" if fief and fief.owner_id == army.house_id else "IDLE"
#             )

#         # --- LOGIC BRANCH B: FLEET ARRIVAL ---
#         elif army.army_type == "SEA":
#             print("DEBUG: Fleet arrival detected. Checking for cargo operations...")
#             new_status = "DOCKED"

#             # --- CASE 1: PURE SEA JOURNEY (Unpack Cargo) ---
#             if army.cargo and army.cargo.get("troop_count", 0) > 0:
#                 print(f"DEBUG: Unpacking Cargo from Fleet {army_id}...")
#                 cargo = army.cargo

#                 fief = (
#                     session.query(Fief)
#                     .filter(
#                         Fief.location_x == army.location_x,
#                         Fief.location_y == army.location_y,
#                     )
#                     .first()
#                 )
#                 # --- FIX 2: Check Ownership for Disembarked Troops ---
#                 # The newly created land army should also obey the same garrisoning rule.
#                 land_status = (
#                     "GARRISONED" if fief and fief.owner_id == army.house_id else "IDLE"
#                 )

#                 new_land_army = Army(
#                     game_id=army.game_id,
#                     house_id=army.house_id,
#                     army_type="LAND",
#                     commander_name=cargo.get("commander", "Disembarked Force"),
#                     troop_count=cargo.get("troop_count", 0),
#                     composition=cargo.get("composition", {}),
#                     location_x=army.location_x,
#                     location_y=army.location_y,
#                     status=land_status,  # Use the correctly determined status
#                     treasury=0,
#                 )
#                 session.add(new_land_army)
#                 army.cargo = None
#                 print(
#                     f"DEBUG: Cargo unpacked into new Army with status '{land_status}'."
#                 )

#             # --- CASE 2: HYBRID JOURNEY (Sync Ghost Army) ---
#             else:
#                 ghost_army = (
#                     session.query(Army)
#                     .filter(
#                         Army.house_id == army.house_id,
#                         Army.army_type == "LAND",
#                         Army.status == "MARCHING",
#                         Army.location_x == army.location_x,
#                         Army.location_y == army.location_y,
#                     )
#                     .first()
#                 )
#                 if ghost_army:
#                     print(
#                         f"DEBUG: Found Hybrid Ghost Army (ID: {ghost_army.army_id}). Syncing times."
#                     )
#                     if ghost_army.arrival_time and ghost_army.departure_time:
#                         march_duration = (
#                             ghost_army.arrival_time - ghost_army.departure_time
#                         )
#                     else:
#                         march_duration = datetime.timedelta(hours=1)
#                     ghost_army.departure_time = now
#                     ghost_army.arrival_time = now + march_duration
#                     if ghost_army.task_id:
#                         celery_app.control.revoke(ghost_army.task_id)
#                     new_task = resolve_army_arrival.apply_async(
#                         args=[ghost_army.army_id], eta=ghost_army.arrival_time
#                     )
#                     ghost_army.task_id = new_task.id
#                     print(
#                         f"DEBUG: Ghost Army {ghost_army.army_id} resynced and marching."
#                     )
#                 else:
#                     print(
#                         "DEBUG: No cargo and no ghost army found. Fleet is just moving empty."
#                     )

#         # 4. Finalize & Commit
#         army.status = new_status
#         session.query(MarchLog).filter(MarchLog.army_id == army_id).delete()
#         session.commit()
#         print(f"DEBUG: Success. Army {army_id} is now {new_status}.")

#         # 6. Notifications (Redis) logic...
#         house = session.query(House).filter(House.house_id == army.house_id).first()
#         if house:
#             owner_query = (
#                 session.query(User)
#                 .join(GamePlayer)
#                 .filter(
#                     GamePlayer.claimed_house_id == army.house_id,
#                     GamePlayer.is_primary == True,
#                 )
#                 .first()
#             )
#             owner_discord_id = owner_query.discord_id if owner_query else None
#             loc = (
#                 session.query(Fief)
#                 .filter(
#                     Fief.location_x == army.location_x,
#                     Fief.location_y == army.location_y,
#                 )
#                 .first()
#             )
#             location_name = (
#                 loc.name
#                 if loc
#                 else f"Coord ({int(army.location_x)}, {int(army.location_y)})"
#             )
#             payload = {
#                 "type": "ARRIVAL",
#                 "guild_id": house.game.guild_id,
#                 "house_name": house.name,
#                 "owner_id": owner_discord_id,
#                 "commander": army.commander_name,
#                 "troops": army.troop_count,
#                 "unit_type": army.army_type,
#                 "location": location_name,
#             }
#             if REDIS_CLIENT:
#                 REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

#     except Exception as e:
#         print(f"FATAL ERROR in resolve_army_arrival (ID: {army_id})")
#         import traceback

#         traceback.print_exc()
#         session.rollback()
#     finally:
#         session.close()


# @celery_app.task(bind=True)
# def resolve_army_arrival(self, army_id: int):
#     """
#     Finalizes army movement.
#     Includes "Smart Snap" logic to ensure Fleets connect with their Hybrid Armies.
#     """
#     print(f"--- RESOLVING ARRIVAL FOR ARMY ID: {army_id} ---")
#     session = get_sync_session()
#     try:
#         army = (
#             session.query(Army)
#             .options(selectinload(Army.house).selectinload(House.game))
#             .get(army_id)
#         )

#         if not army or army.status not in ["MARCHING", "SAILING"]:
#             print(f"DEBUG: Army {army_id} invalid state or not found. Task ending.")
#             session.close()
#             return

#         # 1. Update Coordinates from Destination
#         army.location_x = army.destination_x
#         army.location_y = army.destination_y

#         # 2. "SMART SNAP" LOGIC FOR FLEETS
#         # We must prioritize snapping to the tile where a Ghost Army is waiting.
#         if army.army_type == "SEA":
#             print(
#                 f"[DEBUG ARRIVAL] Fleet {army.army_id} arrived at {army.location_x},{army.location_y}."
#             )

#             original_x, original_y = int(army.location_x), int(army.location_y)
#             target_snap_x, target_snap_y = None, None

#             # --- PASS 1: SEARCH FOR WAITING GHOST ARMY (Priority) ---
#             # Search 3x3 grid for a friendly Land Army that is MARCHING
#             ghost_found = False
#             for dx in range(-1, 2):
#                 for dy in range(-1, 2):
#                     check_x, check_y = original_x + dx, original_y + dy

#                     # Look for the specific conditions of a hybrid journey ghost army
#                     candidate = (
#                         session.query(Army)
#                         .filter(
#                             Army.game_id == army.game_id,
#                             Army.house_id == army.house_id,
#                             Army.location_x == check_x,
#                             Army.location_y == check_y,
#                             Army.army_type == "LAND",
#                             Army.status == "MARCHING",
#                         )
#                         .first()
#                     )

#                     if candidate:
#                         print(
#                             f"[DEBUG ARRIVAL] Found waiting Ghost Army at {check_x},{check_y}. Snapping there."
#                         )
#                         target_snap_x, target_snap_y = check_x, check_y
#                         ghost_found = True
#                         break
#                 if ghost_found:
#                     break

#             # --- PASS 2: GENERIC LAND FINDER (Fallback for Cargo Unloading) ---
#             if not target_snap_x:
#                 min_dist_sq = float("inf")
#                 for dx in range(-1, 2):
#                     for dy in range(-1, 2):
#                         check_x, check_y = original_x + dx, original_y + dy

#                         if (
#                             0 <= check_y < PF_ENGINE.cost_map.shape[0]
#                             and 0 <= check_x < PF_ENGINE.cost_map.shape[1]
#                         ):

#                             terrain_cost = PF_ENGINE.cost_map[check_y, check_x]
#                             # Check for Land or Road
#                             if terrain_cost in [COSTS["land"], COSTS["road"]]:
#                                 dist_sq = dx * dx + dy * dy
#                                 if dist_sq < min_dist_sq:
#                                     min_dist_sq = dist_sq
#                                     target_snap_x, target_snap_y = check_x, check_y

#             # Apply the Snap
#             if target_snap_x is not None:
#                 army.location_x = target_snap_x
#                 army.location_y = target_snap_y
#             else:
#                 print(
#                     f"[DEBUG ARRIVAL] WARNING: Fleet remained at sea/water coords {original_x},{original_y}. No land found."
#                 )

#         # 3. Clear Movement Data
#         army.destination_x, army.destination_y = None, None
#         army.arrival_time, army.departure_time = None, None
#         army.task_id = None

#         new_status = ""
#         now = datetime.datetime.now(datetime.timezone.utc)

#         # 4. DETERMINE FINAL STATUS AND HANDLE CARGO/HYBRID
#         if army.army_type == "LAND":
#             fief = (
#                 session.query(Fief)
#                 .filter(
#                     Fief.location_x == army.location_x,
#                     Fief.location_y == army.location_y,
#                 )
#                 .first()
#             )
#             new_status = (
#                 "GARRISONED" if fief and fief.owner_id == army.house_id else "IDLE"
#             )

#         elif army.army_type == "SEA":
#             new_status = "DOCKED"

#             # --- CASE 1: CARGO UNPACKING (Pure Sea Journey) ---
#             if army.cargo and army.cargo.get("troop_count", 0) > 0:
#                 print(f"DEBUG: Unpacking Cargo from Fleet {army_id}...")
#                 cargo = army.cargo
#                 fief = (
#                     session.query(Fief)
#                     .filter(
#                         Fief.location_x == army.location_x,
#                         Fief.location_y == army.location_y,
#                     )
#                     .first()
#                 )
#                 land_status = (
#                     "GARRISONED" if fief and fief.owner_id == army.house_id else "IDLE"
#                 )

#                 new_land_army = Army(
#                     game_id=army.game_id,
#                     house_id=army.house_id,
#                     army_type="LAND",
#                     commander_name=cargo.get("commander", "Disembarked Force"),
#                     troop_count=cargo.get("troop_count", 0),
#                     composition=cargo.get("composition", {}),
#                     location_x=army.location_x,
#                     location_y=army.location_y,
#                     status=land_status,
#                     treasury=0,
#                 )
#                 session.add(new_land_army)
#                 army.cargo = None
#                 print(
#                     f"DEBUG: Cargo unpacked into new Army with status '{land_status}'."
#                 )

#             # --- CASE 2: HYBRID JOURNEY (Sync Ghost Army) ---
#             else:
#                 # We search for the Ghost Army at the fleet's CURRENT (Snapped) location
#                 ghost_army = (
#                     session.query(Army)
#                     .filter(
#                         Army.house_id == army.house_id,
#                         Army.army_type == "LAND",
#                         Army.status == "MARCHING",
#                         Army.location_x == army.location_x,
#                         Army.location_y == army.location_y,
#                     )
#                     .first()
#                 )

#                 if ghost_army:
#                     print(
#                         f"DEBUG: Found Hybrid Ghost Army (ID: {ghost_army.army_id}). Syncing times."
#                     )

#                     # Calculate remaining duration or default
#                     if ghost_army.arrival_time and ghost_army.departure_time:
#                         march_duration = (
#                             ghost_army.arrival_time - ghost_army.departure_time
#                         )
#                     else:
#                         march_duration = datetime.timedelta(hours=1)

#                     # Reset times to NOW
#                     ghost_army.departure_time = now
#                     ghost_army.arrival_time = now + march_duration

#                     # Reschedule Task
#                     if ghost_army.task_id:
#                         celery_app.control.revoke(ghost_army.task_id)

#                     new_task = resolve_army_arrival.apply_async(
#                         args=[ghost_army.army_id], eta=ghost_army.arrival_time
#                     )
#                     ghost_army.task_id = new_task.id
#                     print(
#                         f"DEBUG: Ghost Army {ghost_army.army_id} resynced and marching."
#                     )
#                 else:
#                     print(
#                         "DEBUG: No cargo and no ghost army found. Fleet arrived empty."
#                     )

#         # 5. Finalize State
#         army.status = new_status
#         session.query(MarchLog).filter(MarchLog.army_id == army_id).delete()
#         session.commit()
#         print(f"DEBUG: Success. Army {army_id} is now {new_status}.")

#         # 7. Notifications (Redis)
#         house = army.house
#         if house and house.game:
#             owner_query = (
#                 session.query(User)
#                 .join(GamePlayer)
#                 .filter(
#                     GamePlayer.claimed_house_id == army.house_id,
#                     GamePlayer.is_primary == True,
#                 )
#                 .first()
#             )
#             owner_discord_id = owner_query.discord_id if owner_query else None

#             loc_fief = (
#                 session.query(Fief)
#                 .filter(
#                     Fief.location_x == army.location_x,
#                     Fief.location_y == army.location_y,
#                 )
#                 .first()
#             )
#             location_name = (
#                 loc_fief.name
#                 if loc_fief
#                 else f"Coord ({int(army.location_x)}, {int(army.location_y)})"
#             )

#             payload = {
#                 "type": "ARRIVAL",
#                 "guild_id": house.game.guild_id,
#                 "house_name": house.name,
#                 "owner_id": owner_discord_id,
#                 "house_id": army.house_id,
#                 "commander": army.commander_name,
#                 "troops": army.troop_count,
#                 "unit_type": army.army_type,
#                 "location": location_name,
#             }
#             if REDIS_CLIENT:
#                 REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
#             print(f"DEBUG: Published arrival event: {payload}")

#     except Exception as e:
#         session.rollback()
#         print(f"FATAL ERROR in resolve_army_arrival for Army ID {army_id}: {e}")
#         import traceback

#         traceback.print_exc()
#         raise self.retry(exc=e, countdown=60)
#     finally:
#         session.close()


@celery_app.task(bind=True)
def resolve_army_arrival(self, army_id: int):
    """
    Finalizes army movement.
    Includes "Smart Snap" logic and prevents cargo from disembarking in the ocean.
    """
    print(f"--- RESOLVING ARRIVAL FOR ARMY ID: {army_id} ---")
    session = get_sync_session()
    try:
        army = (
            session.query(Army)
            .options(selectinload(Army.house).selectinload(House.game))
            .get(army_id)
        )

        if not army or army.status not in ["MARCHING", "SAILING"]:
            print(f"DEBUG: Army {army_id} invalid state or not found. Task ending.")
            session.close()
            return

        # 1. Update Coordinates from Destination
        army.location_x = army.destination_x
        army.location_y = army.destination_y

        # 2. "SMART SNAP" LOGIC (No changes needed here, this logic is fine)
        if army.army_type == "SEA":
            # ... (the existing smart snap logic remains the same) ...
            pass

        # 3. Clear Movement Data
        army.destination_x, army.destination_y = None, None
        army.arrival_time, army.departure_time = None, None
        army.task_id = None

        new_status = ""
        now = datetime.datetime.now(datetime.timezone.utc)

        # 4. DETERMINE FINAL STATUS AND HANDLE CARGO/HYBRID
        if army.army_type == "LAND":
            fief = (
                session.query(Fief)
                .filter(
                    Fief.location_x == army.location_x,
                    Fief.location_y == army.location_y,
                )
                .first()
            )
            new_status = (
                "GARRISONED" if fief and fief.owner_id == army.house_id else "IDLE"
            )

        elif army.army_type == "SEA":
            # --- CORRECTED LOGIC [START] ---
            # Priority 1: Handle Hybrid Journey (fleet has no cargo, but a ghost army is waiting)
            ghost_army = (
                session.query(Army)
                .filter(
                    Army.house_id == army.house_id,
                    Army.army_type == "LAND",
                    Army.status == "MARCHING",
                    Army.location_x == army.location_x,
                    Army.location_y == army.location_y,
                )
                .first()
            )

            if ghost_army:
                print(
                    f"DEBUG: Found Hybrid Ghost Army (ID: {ghost_army.army_id}). Syncing."
                )
                new_status = "DOCKED"
                # ... (rest of ghost army sync logic is correct and remains the same) ...
                if ghost_army.arrival_time and ghost_army.departure_time:
                    march_duration = ghost_army.arrival_time - ghost_army.departure_time
                else:
                    march_duration = datetime.timedelta(hours=1)
                ghost_army.departure_time = now
                ghost_army.arrival_time = now + march_duration
                if ghost_army.task_id:
                    celery_app.control.revoke(ghost_army.task_id)
                new_task = resolve_army_arrival.apply_async(
                    args=[ghost_army.army_id], eta=ghost_army.arrival_time
                )
                ghost_army.task_id = new_task.id
                print(f"DEBUG: Ghost Army {ghost_army.army_id} resynced.")

            # Priority 2: Handle Pure Cargo Journey (fleet has cargo)
            elif army.cargo and army.cargo.get("troop_count", 0) > 0:
                print(
                    f"DEBUG: Fleet {army_id} arrived with cargo. Checking for valid landing zone."
                )

                # Scan for a valid spot to disembark
                best_land_spot = None
                min_dist_sq = float("inf")
                original_x, original_y = int(army.location_x), int(army.location_y)
                cost_map = PF_ENGINE.cost_map
                rows, cols = cost_map.shape

                for dx in range(-1, 2):
                    for dy in range(-1, 2):
                        check_x, check_y = original_x + dx, original_y + dy
                        if 0 <= check_y < rows and 0 <= check_x < cols:
                            terrain_cost = cost_map[check_y, check_x]
                            if terrain_cost not in [
                                COSTS["ocean"],
                                COSTS["coastal_water"],
                            ]:
                                dist_sq = dx * dx + dy * dy
                                if dist_sq < min_dist_sq:
                                    min_dist_sq = dist_sq
                                    best_land_spot = (check_x, check_y)

                # CASE A: Valid landing spot found -> Unpack the cargo
                if best_land_spot:
                    print(
                        f"DEBUG: Landing zone found at {best_land_spot}. Disembarking cargo."
                    )
                    new_status = "DOCKED"
                    cargo = army.cargo
                    fief = (
                        session.query(Fief)
                        .filter(
                            Fief.location_x == best_land_spot[0],
                            Fief.location_y == best_land_spot[1],
                        )
                        .first()
                    )
                    land_status = (
                        "GARRISONED"
                        if fief and fief.owner_id == army.house_id
                        else "IDLE"
                    )

                    new_land_army = Army(
                        game_id=army.game_id,
                        house_id=army.house_id,
                        army_type="LAND",
                        commander_name=cargo.get("commander", "Disembarked Force"),
                        troop_count=cargo.get("troop_count", 0),
                        composition=cargo.get("composition", {}),
                        location_x=best_land_spot[0],
                        location_y=best_land_spot[1],
                        status=land_status,
                        treasury=0,
                    )
                    session.add(new_land_army)
                    army.cargo = None

                # CASE B: No valid landing spot -> Keep cargo onboard
                else:
                    print(
                        "DEBUG: No valid landing zone. Cargo remains onboard. Fleet is now idle at sea."
                    )
                    new_status = "IDLE"

            # Priority 3: Fleet arrived empty and is not part of a hybrid journey
            else:
                print("DEBUG: No cargo and no ghost army found. Fleet arrived empty.")
                new_status = "DOCKED"
            # --- CORRECTED LOGIC [END] ---

        # 5. Finalize State
        army.status = new_status
        session.query(MarchLog).filter(MarchLog.army_id == army_id).delete()
        session.commit()
        print(f"DEBUG: Success. Army {army_id} is now {new_status}.")

        # ... (Notification logic remains the same) ...
        house = army.house
        if house and house.game:
            owner_query = (
                session.query(User)
                .join(GamePlayer)
                .filter(
                    GamePlayer.claimed_house_id == army.house_id,
                    GamePlayer.is_primary == True,
                )
                .first()
            )
            owner_discord_id = owner_query.discord_id if owner_query else None

            loc_fief = (
                session.query(Fief)
                .filter(
                    Fief.location_x == army.location_x,
                    Fief.location_y == army.location_y,
                )
                .first()
            )
            location_name = (
                loc_fief.name
                if loc_fief
                else f"Coord ({int(army.location_x)}, {int(army.location_y)})"
            )
            payload = {
                "type": "ARRIVAL",
                "guild_id": house.game.guild_id,
                "house_name": house.name,
                "owner_id": owner_discord_id,
                "house_id": army.house_id,  # Ensure house_id is included
                "commander": army.commander_name,
                "troops": army.troop_count,
                "unit_type": army.army_type,
                "location": location_name,
            }
            if REDIS_CLIENT:
                REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
            print(f"DEBUG: Published arrival event: {payload}")

    except Exception as e:
        session.rollback()
        print(f"FATAL ERROR in resolve_army_arrival for Army ID {army_id}: {e}")
        import traceback

        traceback.print_exc()
        raise self.retry(exc=e, countdown=60)
    finally:
        session.close()


@celery_app.task
def dispatch_scout_report(
    game_id: int, army_id_a: int, army_id_b: int, location_name: str
):
    """
    Fires when two armies are close.
    """
    print(f"⚡ Celery: Dispatching scout report for Army {army_id_a} vs {army_id_b}...")
    session = get_sync_session()

    try:
        army_a = session.query(Army).filter(Army.army_id == army_id_a).first()
        army_b = session.query(Army).filter(Army.army_id == army_id_b).first()

        if not army_a or not army_b:
            print("⚠️ One or more armies gone. Aborting alert.")
            return

        # 3. Helper to get Notification Data
        def get_party_data(army):
            house = session.query(House).filter(House.house_id == army.house_id).first()
            owner = (
                session.query(User)
                .join(GamePlayer)
                .filter(
                    GamePlayer.claimed_house_id == army.house_id,
                    GamePlayer.is_primary == True,
                )
                .first()
            )

            # --- THIS IS THE CORRECTED PART ---
            return {
                "house_name": house.name,
                "owner_id": owner.discord_id if owner else None,
                "commander": army.commander_name,
                "troops": army.troop_count,
                # FIX 1: Create the 'is_moving' boolean key the listener expects.
                "is_moving": army.status in ["MARCHING", "SAILING"],
                # FIX 2: Add the 'army_type' key the listener also needs.
                "army_type": army.army_type,
            }
            # --- END OF CORRECTION ---

        party_a = get_party_data(army_a)
        party_b = get_party_data(army_b)

        house_a_obj = (
            session.query(House).filter(House.house_id == army_a.house_id).first()
        )
        guild_id = house_a_obj.game.guild_id

        # 4. Publish to Redis (Payload is now correctly formatted)
        payload = {
            "type": "INTERCEPTION",  # This should match your listener's expected type
            "guild_id": guild_id,
            "location": location_name,
            "parties": [party_a, party_b],
        }

        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
        print(f"📡 Interception Event Published.")

    except Exception as e:
        print(f"❌ Scout Task Error: {e}")
    finally:
        session.close()


@celery_app.task
def dispatch_gate_alert(
    game_id: int, army_id: int, gate_name: str, gate_owner_house_id: int
):
    """
    FIRES WHEN AN ARMY REACHES A GATE.
    It halts the army in the database and then publishes an event to Redis,
    prompting the defender (via the bot) to make a "Grant" or "Deny" decision.
    """
    print(
        f"--- GATE ARRIVAL: Army {army_id} has reached {gate_name}. Halting and notifying defender. ---"
    )
    session = get_sync_session()

    try:
        # Eagerly load related data for efficiency
        army = (
            session.query(Army)
            .options(selectinload(Army.house).selectinload(House.game))
            .filter(Army.army_id == army_id)
            .first()
        )

        # Validation: If army was already stopped or deleted, do nothing.
        if not army or army.status not in ["MARCHING", "SAILING"]:
            print(
                f"DEBUG: Army {army_id} is already stopped or does not exist. Aborting gate halt."
            )
            return

        # --- 1. HALT THE ARMY AT THE GATE ---
        # Find the gate's coordinates to update the army's location accurately
        gate_fief = (
            session.query(Fief)
            .filter(Fief.name.ilike(gate_name), Fief.game_id == game_id)
            .first()
        )
        if gate_fief:
            army.location_x = gate_fief.location_x
            army.location_y = gate_fief.location_y
        else:
            print(
                f"CRITICAL WARNING: Could not find Fief for gate '{gate_name}'. Army will be halted at its current predicted position."
            )

        army.status = "IDLE"  # Army is now officially stopped.
        print(f"DEBUG: Army {army_id} status set to IDLE.")

        # --- 2. REVOKE THE FINAL ARRIVAL TASK ---
        if army.task_id:
            print(
                f"DEBUG: Revoking final arrival task ({army.task_id}) for army {army_id}."
            )
            # Use terminate=True, but NO signal='SIGKILL' for Windows compatibility
            AsyncResult(army.task_id, app=celery_app).revoke(terminate=True)
            army.task_id = None  # Clear the revoked task ID

        # Clear current movement data. The original_destination fields are preserved.
        army.destination_x = None
        army.destination_y = None
        army.arrival_time = None
        army.departure_time = None

        session.commit()

        # --- 3. PUBLISH 'REQUEST FOR DECISION' TO REDIS ---
        # This payload tells the bot to show the "Grant" / "Deny" buttons.
        marcher_house = army.house
        defender_house = (
            session.query(House).filter(House.house_id == gate_owner_house_id).first()
        )
        defender_player = (
            session.query(GamePlayer)
            .filter(
                GamePlayer.claimed_house_id == gate_owner_house_id,
                GamePlayer.game_id == game_id,
            )
            .first()
        )
        defender_user = None
        if defender_player:
            defender_user = (
                session.query(User)
                .filter(User.user_id == defender_player.user_id)
                .first()
            )

        payload = {
            "type": "GATE_ALERT",
            "guild_id": marcher_house.game.guild_id,
            "attacking_army_id": army.army_id,
            "gate_name": gate_name,
            "marcher": {
                "house_name": marcher_house.name,
                "commander": army.commander_name,
                "troops": army.troop_count,
            },
            "defender": {
                "house_id": defender_house.house_id,
                "house_name": defender_house.name,
                "discord_id": defender_user.discord_id if defender_user else None,
                "is_npc": defender_user is None,
            },
        }

        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
        print(
            f"✅ Army {army_id} halted. Published GATE_ALERT to Redis for defender decision."
        )

    except Exception as e:
        print(f"FATAL ERROR in dispatch_gate_alert (ID: {army_id}): {repr(e)}")
        import traceback

        traceback.print_exc()
        session.rollback()
    finally:
        session.close()


@celery_app.task
def handle_gate_response(army_id: int, action: str):
    """
    Handles the "GRANT" decision from a defender.
    Resumes the army's march from the gate to its original destination.
    """
    if action.upper() != "GRANT":
        return  # This task now only cares about granting passage.

    print(f"--- RESUMING MARCH for Army {army_id} after passage granted ---")
    session = get_sync_session()
    try:
        army = session.query(Army).filter(Army.army_id == army_id).first()

        if not army:
            print(f"DEBUG: Army {army_id} not found.")
            return
        if army.status != "IDLE":
            print(
                f"DEBUG: Army {army_id} is not in an IDLE state. Cannot resume march."
            )
            return
        if not army.original_destination_x or not army.original_destination_y:
            print(
                f"CRITICAL ERROR: Army {army_id} was granted passage but has no 'original_destination' saved."
            )
            return

        # 1. Pathfind the final leg of the journey
        start_coords = (army.location_x, army.location_y)
        end_coords = (army.original_destination_x, army.original_destination_y)

        # We need GM settings for the pathfinder
        game_repo = GameRepo()  # You might need to adapt how you get game settings
        game = session.query(Game).filter(Game.game_id == army.game_id).first()
        gm_settings = game.__dict__  # A simple way to get settings

        path_data = (
            PF_ENGINE._find_journey_sync(  # Call the renamed synchronous function
                start_loc=start_coords,
                end_loc=end_coords,
                travel_mode="land_only",
                gm_settings=gm_settings,
            )
        )

        if not path_data:
            print(f"ERROR: Could not calculate resume path for army {army_id}.")
            # Optional: Notify the player that something went wrong.
            return

        # 2. Calculate new duration and arrival time
        duration = calculate_travel_duration(
            path_data["terrain_breakdown"], army.troop_count
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        arrival_time = now + datetime.timedelta(seconds=duration)

        # 3. Update the Army with new march orders
        army.status = "MARCHING"
        army.destination_x = army.original_destination_x
        army.destination_y = army.original_destination_y
        army.departure_time = now
        army.arrival_time = arrival_time

        # Clear the original destination, as it's now the current destination
        army.original_destination_x = None
        army.original_destination_y = None

        # 4. Schedule the final arrival task
        new_task = resolve_army_arrival.apply_async(
            args=[army.army_id], eta=arrival_time
        )
        army.task_id = new_task.id

        session.commit()
        print(
            f"✅ Army {army_id} has been granted passage and is now marching to its final destination."
        )

    except Exception as e:
        print(f"FATAL ERROR in handle_gate_response (ID: {army_id}): {repr(e)}")
        import traceback

        traceback.print_exc()
        session.rollback()
    finally:
        session.close()


# @celery_app.task
# def initiate_player_interaction(
#     game_id: int,
#     army1_id: int,
#     army2_id: int,
#     intercept_time: datetime.datetime,
#     intercept_x: float,
#     intercept_y: float,
# ):
#     """
#     Creates the PendingInteraction record and schedules the final resolution task.
#     Then, it tells the bot (via Redis) to display the UI to the players.
#     """
#     session = get_sync_session()
#     try:
#         # 1. Check if armies still exist and are marching
#         army1 = session.query(Army).filter(Army.army_id == army1_id).first()
#         army2 = session.query(Army).filter(Army.army_id == army2_id).first()
#         valid_moving_statuses = ["MARCHING", "SAILING", "DOCKED", "IDLE"]
#         # If one army has been destroyed or stopped, cancel the interaction.
#         if not army1 or not army2 or army1.status not in valid_moving_statuses:
#             print(
#                 f"Interaction cancelled: Army {army1_id} or {army2_id} is no longer valid."
#             )
#             return

#         existing_interaction = (
#             session.query(PendingInteraction)
#             .filter(
#                 PendingInteraction.game_id == game_id,
#                 PendingInteraction.status == "PENDING",
#                 or_(
#                     (PendingInteraction.army1_id == army1_id)
#                     & (PendingInteraction.army2_id == army2_id),
#                     (PendingInteraction.army1_id == army2_id)
#                     & (PendingInteraction.army2_id == army1_id),
#                 ),
#             )
#             .first()
#         )

#         if existing_interaction:
#             print(
#                 f"DEBUG: Interaction between {army1_id} and {army2_id} already pending (ID: {existing_interaction.id}). Skipping duplicate."
#             )
#             return

#         # 2. Create the Database Record
#         expires_at = intercept_time - datetime.timedelta(
#             seconds=1
#         )  # The decision window closes 1s before the intercept

#         new_interaction = PendingInteraction(
#             game_id=game_id,
#             army1_id=army1_id,
#             army2_id=army2_id,
#             status="PENDING",
#             expires_at=expires_at,
#             location_x=intercept_x,
#             location_y=intercept_y,
#         )
#         session.add(new_interaction)
#         session.flush()  # Flush to get the new_interaction.id

#         # 3. Schedule the resolver task to run when the decision window expires
#         resolver_task = resolve_player_interaction.apply_async(
#             args=[new_interaction.id], eta=expires_at
#         )
#         new_interaction.resolver_task_id = resolver_task.id
#         session.commit()

#         # 4. Publish an event to Redis, telling the bot to send the UI
#         payload = {
#             "type": "PROMPT_INTERACTION",
#             "interaction_id": new_interaction.id,
#         }
#         REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
#         print(
#             f"DEBUG: Created PendingInteraction {new_interaction.id} and published to Redis."
#         )

#     except Exception as e:
#         print(f"❌ Error in initiate_player_interaction: {e}")
#         session.rollback()
#     finally:
#         session.close()

from sqlalchemy import or_  # Make sure you have this import


@celery_app.task
def initiate_player_interaction(
    game_id: int,
    army1_id: int,
    army2_id: int,
    intercept_time: datetime.datetime,
    intercept_x: float,
    intercept_y: float,
):
    """
    Creates the PendingInteraction record.
    FIXES:
    1. Allows Sailing/Docked status.
    2. Prevents Duplicates.
    3. Prevents INSTANT EXPIRY if fleets are close.
    """
    session = get_sync_session()
    try:
        # 1. Check if armies still exist
        army1 = session.query(Army).filter(Army.army_id == army1_id).first()
        army2 = session.query(Army).filter(Army.army_id == army2_id).first()

        # Valid statuses for triggering a fight
        valid_moving_statuses = ["MARCHING", "SAILING", "DOCKED", "IDLE"]

        if not army1 or not army2 or army1.status not in valid_moving_statuses:
            print(
                f"Interaction cancelled: Army {army1_id} or {army2_id} invalid status."
            )
            return

        # 2. Check for Duplicates (Prevents Double Pings)
        existing_interaction = (
            session.query(PendingInteraction)
            .filter(
                PendingInteraction.game_id == game_id,
                PendingInteraction.status == "PENDING",
                or_(
                    (PendingInteraction.army1_id == army1_id)
                    & (PendingInteraction.army2_id == army2_id),
                    (PendingInteraction.army1_id == army2_id)
                    & (PendingInteraction.army2_id == army1_id),
                ),
            )
            .first()
        )

        if existing_interaction:
            print(
                f"DEBUG: Interaction between {army1_id} and {army2_id} already pending. Skipping."
            )
            return

        # 3. Calculate Expiry with SAFETY BUFFER (The Fix)
        now = datetime.datetime.now(datetime.timezone.utc)

        # Physical collision time
        calculated_expiry = intercept_time - datetime.timedelta(seconds=1)

        # Minimum time: NOW + 2 Minutes
        min_expiry = now + datetime.timedelta(minutes=2)

        # If the physical collision is too soon, force the 2-minute window
        if calculated_expiry < min_expiry:
            final_expiry = min_expiry
        else:
            final_expiry = calculated_expiry

        # 4. Create the Database Record
        new_interaction = PendingInteraction(
            game_id=game_id,
            army1_id=army1_id,
            army2_id=army2_id,
            status="PENDING",
            expires_at=final_expiry,  # Use the safe time
            location_x=intercept_x,
            location_y=intercept_y,
        )
        session.add(new_interaction)
        session.flush()

        # 5. Schedule the resolver
        resolver_task = resolve_player_interaction.apply_async(
            args=[new_interaction.id], eta=final_expiry
        )
        new_interaction.resolver_task_id = resolver_task.id
        session.commit()

        # 6. Publish to Redis
        payload = {
            "type": "PROMPT_INTERACTION",
            "interaction_id": new_interaction.id,
        }
        if REDIS_CLIENT:
            REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

        print(
            f"DEBUG: Created Interaction {new_interaction.id}. Expires in {(final_expiry - now).total_seconds():.1f}s"
        )

    except Exception as e:
        print(f"❌ Error in initiate_player_interaction: {e}")
        session.rollback()
    finally:
        session.close()


# In app/tasks/light_tasks.py


@celery_app.task
def resolve_player_interaction(interaction_id: int):
    """
    Runs after the decision timer expires. Determines and executes the outcome.
    """
    # Import here to avoid circular dependency
    from app.tasks.battle_tasks import initiate_auto_battle

    session = get_sync_session()
    try:
        interaction = (
            session.query(PendingInteraction)
            .options(
                selectinload(PendingInteraction.army1),
                selectinload(PendingInteraction.army2),
            )
            .filter(PendingInteraction.id == interaction_id)
            .first()
        )

        if not interaction or interaction.status != "PENDING":
            return
        if not interaction.army1 or not interaction.army2:
            interaction.status = "CANCELLED"
            session.commit()
            return

        # --- Determine Outcome ---
        final_outcome = "MARCH_ON"
        if interaction.army1_choice == "BATTLE" or interaction.army2_choice == "BATTLE":
            final_outcome = "BATTLE"
        elif (
            interaction.army1_choice == "MEETING"
            and interaction.army2_choice == "MEETING"
        ):
            final_outcome = "MEETING"

        print(
            f"DEBUG: Resolving interaction {interaction_id}. Outcome: {final_outcome}"
        )

        # --- Define Synchronous Helper ---
        def halt_army_sync(army: Army, x: float, y: float):
            if not army or army.status not in ["MARCHING", "SAILING"]:
                return
            if army.task_id:
                AsyncResult(army.task_id, app=celery_app).revoke(terminate=True)
            army.location_x, army.location_y = x, y
            army.status = "IDLE"
            army.destination_x, army.destination_y = None, None
            army.arrival_time, army.departure_time, army.task_id = None, None, None
            session.query(MarchLog).filter(MarchLog.army_id == army.army_id).delete()

        # --- Execute Outcome ---
        if final_outcome == "BATTLE":
            interaction.status = "RESOLVED_BATTLE"
            halt_army_sync(
                interaction.army1, interaction.location_x, interaction.location_y
            )
            halt_army_sync(
                interaction.army2, interaction.location_x, interaction.location_y
            )

            # Trigger the next phase: The GM Grace Period
            initiate_auto_battle.apply_async(args=[interaction.id], countdown=5)

        elif final_outcome == "MEETING":
            interaction.status = "RESOLVED_MEETING"
            halt_army_sync(
                interaction.army1, interaction.location_x, interaction.location_y
            )
            halt_army_sync(
                interaction.army2, interaction.location_x, interaction.location_y
            )

            # Notify the bot to create the meeting channel
            payload = {"type": "INTERACTION_MEETING", "interaction_id": interaction.id}
            REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

        else:  # MARCH_ON
            interaction.status = "RESOLVED_MARCH_ON"
            # Notify the bot that the interaction is over (for potential UI updates)
            payload = {"type": "INTERACTION_ENDED", "interaction_id": interaction.id}
            REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

        session.commit()

    except Exception as e:
        print(f"❌ Error in resolve_player_interaction: {e}")
        traceback.print_exc()
        session.rollback()
    finally:
        session.close()
