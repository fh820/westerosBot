from app.celery_app import celery_app
from app.db.sync_db import get_sync_session
from app.db.models import Army, House, GamePlayer, User, Fief, Game, MarchLog
from sqlalchemy import select
import json
import redis
import os
import traceback
import datetime

# Connect to Redis to publish events
REDIS_CLIENT = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


@celery_app.task
def resolve_army_arrival(army_id: int):
    """
    Executes when an army or fleet arrives.
    - Handles Hybrid Journeys (Sail -> March) by syncing the Ghost Army.
    - Handles Pure Sea Journeys by unpacking cargo into a new Army.
    """
    print(f"--- RESOLVING ARRIVAL FOR ARMY ID: {army_id} ---")
    session = get_sync_session()

    try:
        army = session.query(Army).filter(Army.army_id == army_id).first()

        if not army or army.status not in ["MARCHING", "SAILING"]:
            print(f"DEBUG: Army {army_id} invalid state or not found.")
            return

        # 1. Update Coordinates & Clear Movement Data
        army.location_x = army.destination_x
        army.location_y = army.destination_y
        army.destination_x = None
        army.destination_y = None
        army.arrival_time = None
        army.departure_time = None
        army.task_id = None

        new_status = ""
        now = datetime.datetime.now(datetime.timezone.utc)

        # --- LOGIC BRANCH A: LAND ARMY ---
        if army.army_type == "LAND":
            fief = (
                session.query(Fief)
                .filter(
                    Fief.location_x == army.location_x,
                    Fief.location_y == army.location_y,
                )
                .first()
            )
            new_status = "GARRISONED" if fief else "IDLE"

        # --- LOGIC BRANCH B: FLEET ARRIVAL ---
        elif army.army_type == "SEA":
            print("DEBUG: Fleet arrival detected. Checking for cargo operations...")
            new_status = "DOCKED"  # Or "IDLE" depending on your preference

            # --- CASE 1: PURE SEA JOURNEY (Unpack Cargo) ---
            # If the fleet has cargo data, it means no Ghost Army was created.
            # We must create the land army NOW.
            if army.cargo and army.cargo.get("troop_count", 0) > 0:
                print(f"DEBUG: Unpacking Cargo from Fleet {army_id}...")

                cargo = army.cargo

                # Check for fief at arrival point
                fief = (
                    session.query(Fief)
                    .filter(
                        Fief.location_x == army.location_x,
                        Fief.location_y == army.location_y,
                    )
                    .first()
                )
                land_status = "GARRISONED" if fief else "IDLE"

                # Create the new army
                new_land_army = Army(
                    game_id=army.game_id,
                    house_id=army.house_id,
                    army_type="LAND",
                    commander_name=cargo.get("commander", "Disembarked Force"),
                    troop_count=cargo.get("troop_count", 0),
                    composition=cargo.get("composition", {}),
                    location_x=army.location_x,
                    location_y=army.location_y,
                    status=land_status,
                    treasury=0,
                )
                session.add(new_land_army)

                # Clear the fleet's cargo so troops aren't duplicated
                army.cargo = None
                print("DEBUG: Cargo unpacked into new Army.")

            # --- CASE 2: HYBRID JOURNEY (Sync Ghost Army) ---
            # If cargo is empty, it means a Ghost Army was already created
            # and is waiting in the database with status='MARCHING'.
            else:
                # Find the Land Army belonging to this house, at this location,
                # that is currently set to 'MARCHING'.
                ghost_army = (
                    session.query(Army)
                    .filter(
                        Army.house_id == army.house_id,
                        Army.army_type == "LAND",
                        Army.status
                        == "MARCHING",  # Crucial: Service sets this to MARCHING
                        Army.location_x == army.location_x,
                        Army.location_y == army.location_y,
                    )
                    .first()
                )

                if ghost_army:
                    print(
                        f"DEBUG: Found Hybrid Ghost Army (ID: {ghost_army.army_id}). Syncing times."
                    )

                    # The Ghost Army has a departure_time set to the *estimated* arrival.
                    # We accept the drift and update its times to start NOW.

                    # Calculate how long the march was supposed to be
                    if ghost_army.arrival_time and ghost_army.departure_time:
                        march_duration = (
                            ghost_army.arrival_time - ghost_army.departure_time
                        )
                    else:
                        march_duration = datetime.timedelta(hours=1)  # Fallback

                    # Reset start time to NOW to fix any lag drift
                    ghost_army.departure_time = now
                    ghost_army.arrival_time = now + march_duration

                    # We must Reschedule the Celery Task because the original task
                    # might have fired too early or late due to drift.
                    if ghost_army.task_id:
                        celery_app.control.revoke(ghost_army.task_id)

                    new_task = resolve_army_arrival.apply_async(
                        args=[ghost_army.army_id], eta=ghost_army.arrival_time
                    )
                    ghost_army.task_id = new_task.id

                    print(
                        f"DEBUG: Ghost Army {ghost_army.army_id} resynced and marching."
                    )
                else:
                    print(
                        "DEBUG: No cargo and no ghost army found. Fleet is just moving empty."
                    )

        # 4. Finalize & Commit
        army.status = new_status
        # Clear logs
        session.query(MarchLog).filter(MarchLog.army_id == army_id).delete()

        # 5. Commit Changes
        session.commit()
        print(f"DEBUG: Success. Army {army_id} is now {new_status}.")

        # 6. Notifications (Redis) logic...
        # (Keep your existing notification code here)
        house = session.query(House).filter(House.house_id == army.house_id).first()
        if house:
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

            # Get location name
            loc = (
                session.query(Fief)
                .filter(
                    Fief.location_x == army.location_x,
                    Fief.location_y == army.location_y,
                )
                .first()
            )
            location_name = (
                loc.name
                if loc
                else f"Coord ({int(army.location_x)}, {int(army.location_y)})"
            )

            payload = {
                "type": "ARRIVAL",
                "guild_id": house.game.guild_id,
                "house_name": house.name,
                "owner_id": owner_discord_id,
                "commander": army.commander_name,
                "troops": army.troop_count,
                "unit_type": army.army_type,
                "location": location_name,
            }
            # Publish
            if REDIS_CLIENT:
                REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

    except Exception as e:
        print(f"FATAL ERROR in resolve_army_arrival (ID: {army_id})")
        import traceback

        traceback.print_exc()
        session.rollback()
    finally:
        session.close()


# @celery_app.task
# def dispatch_scout_report(
#     game_id: int, army_id_a: int, army_id_b: int, location_name: str
# ):
#     """
#     Fires when two armies are close.
#     """
#     print(f"⚡ Celery: Dispatching scout report for Army {army_id_a} vs {army_id_b}...")
#     session = get_sync_session()

#     try:
#         # 1. Fetch Armies
#         army_a = session.query(Army).filter(Army.army_id == army_id_a).first()
#         army_b = session.query(Army).filter(Army.army_id == army_id_b).first()

#         # 2. Validate (Are they still active?)
#         if not army_a or not army_b:
#             print("⚠️ One or more armies gone. Aborting alert.")
#             return

#         # We alert even if stopped, as long as they are close.
#         # But typically A should be marching.

#         # 3. Helper to get Notification Data
#         def get_party_data(army):
#             house = session.query(House).filter(House.house_id == army.house_id).first()
#             # Find Primary User
#             owner = (
#                 session.query(User)
#                 .join(GamePlayer)
#                 .filter(
#                     GamePlayer.claimed_house_id == army.house_id,
#                     GamePlayer.is_primary == True,
#                 )
#                 .first()
#             )

#             return {
#                 "house_name": house.name,
#                 "owner_id": owner.discord_id if owner else None,
#                 "commander": army.commander_name,
#                 "troops": army.troop_count,
#                 "status": army.status,
#             }

#         party_a = get_party_data(army_a)
#         party_b = get_party_data(army_b)

#         # Get Guild ID from House A
#         house_a_obj = (
#             session.query(House).filter(House.house_id == army_a.house_id).first()
#         )
#         guild_id = house_a_obj.game.guild_id

#         # 4. Publish to Redis
#         payload = {
#             "type": "INTERCEPTION",
#             "guild_id": guild_id,
#             "location": location_name,
#             "parties": [party_a, party_b],
#         }

#         REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
#         print(f"📡 Interception Event Published.")

#     except Exception as e:
#         print(f"❌ Scout Task Error: {e}")
#     finally:
#         session.close()


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
