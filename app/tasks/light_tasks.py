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


@celery_app.task(bind=True)
def resolve_army_arrival(self, army_id: int):
    """
    Finalizes army movement. This version is IDEMPOTENT to prevent race conditions
    and optimized to reduce database queries.
    """
    from sqlalchemy.orm.attributes import flag_modified

    session = get_sync_session()
    try:
        # Start a transaction. The lock we acquire will be held until this
        # transaction is either committed or rolled back.
        session.begin()

        # =============================================================
        # THE FIX: Eagerly load all data and acquire a database lock.
        # `with_for_update()` tells Postgres: "Lock this row. Do not let any
        # other transaction read or write to it until I am done."
        # =============================================================
        army = (
            session.query(Army)
            .filter_by(army_id=army_id)
            .options(selectinload(Army.house).selectinload(House.game))
            .with_for_update()  # <-- This is the lock.
            .first()
        )

        # THE CRITICAL CHECK: This is now race-condition-proof.
        # If a duplicate task runs, it will wait at the query above. By the time
        # it gets the lock, the first task will have already changed the status,
        # so this check will correctly fail, preventing a duplicate notification.
        if not army or army.status not in ["MARCHING", "SAILING"]:
            session.commit()  # End the transaction to release the lock.
            session.close()
            return

        # We now have an exclusive lock on the army. Proceed with game logic.

        # 1. Update Coordinates
        army.location_x = army.destination_x
        army.location_y = army.destination_y

        # 2. Clear Movement Data
        army.destination_x, army.destination_y = None, None
        army.arrival_time, army.departure_time, army.task_id = None, None, None

        new_status = ""
        now = datetime.datetime.now(datetime.timezone.utc)

        # 3. HANDLE DISEMBARKATION (Hybrid Journey)
        if army.army_type == "SEA" and army.cargo and "pending_march" in army.cargo:
            p = army.cargo["pending_march"]

            # Create the Land Army at the landing zone
            land_army = Army(
                game_id=army.game_id,
                house_id=army.house_id,
                army_type="LAND",
                commander_name=army.cargo.get(
                    "commander", f"Host of {army.commander_name}"
                ),
                troop_count=army.cargo.get("troop_count", 0),
                composition=army.cargo.get("composition", {}),
                location_x=army.location_x,
                location_y=army.location_y,
                destination_x=p["dest_x"],
                destination_y=p["dest_y"],
                status="MARCHING",
                departure_time=now,
                arrival_time=now + datetime.timedelta(seconds=p["duration"]),
            )
            session.add(land_army)
            session.flush()  # Get land_army.army_id for logging and task scheduling

            # Log the trajectory for the land leg
            path_points = p.get("path", [])
            march_logs_to_add = [
                MarchLog(
                    army_id=land_army.army_id, game_id=army.game_id, x=pt[0], y=pt[1]
                )
                for pt in path_points
            ]
            session.add_all(march_logs_to_add)

            # Schedule final destination arrival
            new_task = resolve_army_arrival.apply_async(
                args=[land_army.army_id], eta=land_army.arrival_time
            )
            land_army.task_id = new_task.id

            # Clear Fleet Cargo
            army.cargo = None
            flag_modified(army, "cargo")
            new_status = "DOCKED"

        elif army.army_type == "LAND":
            fief = (
                session.query(Fief)
                .filter_by(location_x=army.location_x, location_y=army.location_y)
                .first()
            )
            new_status = (
                "GARRISONED" if fief and fief.owner_id == army.house_id else "IDLE"
            )
        else:
            new_status = "DOCKED"

        army.status = new_status
        session.query(MarchLog).filter_by(army_id=army_id).delete()

        # 4. NOTIFICATION DATA (This will now only run ONCE)
        owner_data = (
            session.query(User.discord_id, GamePlayer.private_channel_id)
            .join(GamePlayer)
            .where(
                GamePlayer.claimed_house_id == army.house_id,
                GamePlayer.game_id == army.game_id,
                GamePlayer.is_primary == True,
            )
            .first()
        )

        fief_at_loc = (
            session.query(Fief)
            .filter_by(location_x=army.location_x, location_y=army.location_y)
            .first()
        )
        loc_name = (
            fief_at_loc.name
            if fief_at_loc
            else f"Coord ({int(army.location_x)}, {int(army.location_y)})"
        )

        payload = {
            "type": "ARRIVAL",
            "guild_id": army.house.game.guild_id,
            "house_name": army.house.name,
            "house_id": army.house_id,
            "owner_id": None,
            "private_channel_id": owner_data.private_channel_id if owner_data else None,
            "commander": army.commander_name,
            "troops": army.troop_count,
            "unit_type": army.army_type,
            "location": loc_name,
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
        session.commit()

    except Exception as e:
        session.rollback()  # On any error, undo all changes and release the lock.
        # Retry the task later. It's safe now because of the idempotent check.
        raise self.retry(exc=e, countdown=60)
    finally:
        session.close()  # Always close the session to return the connection to the pool.


@celery_app.task
def dispatch_scout_report(
    game_id: int, army_id_a: int, army_id_b: int, location_name: str
):
    """Fires when two armies are close. Includes Locked Channel IDs in payload."""
    session = get_sync_session()
    try:

        def get_party_info(a_id):
            army = session.query(Army).get(a_id)
            player = (
                session.query(User.discord_id, GamePlayer.private_channel_id)
                .join(GamePlayer)
                .where(
                    GamePlayer.claimed_house_id == army.house_id,
                    GamePlayer.game_id == game_id,
                )
                .first()
            )
            return {
                "house_name": army.house.name,
                "owner_id": None,
                "private_channel_id": player.private_channel_id if player else None,
                "commander": army.commander_name,
                "troops": army.troop_count,
                "army_type": army.army_type,
                "is_moving": army.status in ["MARCHING", "SAILING"],
            }

        party_a = get_party_info(army_id_a)
        party_b = get_party_info(army_id_b)
        game = session.query(Game).get(game_id)

        payload = {
            "type": "INTERCEPTION",
            "guild_id": game.guild_id,
            "location": location_name,
            "parties": [party_a, party_b],
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
    finally:
        session.close()


@celery_app.task
def dispatch_gate_alert(
    game_id: int, army_id: int, gate_name: str, gate_owner_house_id: int
):
    """
    Halts army at a gate, SAVES their destination for resumption, and pings defender.
    """
    session = get_sync_session()
    try:
        # Load Army with House data
        army = session.query(Army).options(selectinload(Army.house)).get(army_id)

        # Validation: Check if army exists and is actually moving
        # If it's already IDLE, the task might be a duplicate or stale
        if not army or army.status not in ["MARCHING", "SAILING"]:
            return

        # 1. Snap Location to the Gate Fief (Visual clarity)
        gate_fief = (
            session.query(Fief)
            .filter(Fief.name.ilike(gate_name), Fief.game_id == game_id)
            .first()
        )
        if gate_fief:
            army.location_x, army.location_y = (
                gate_fief.location_x,
                gate_fief.location_y,
            )

        # 2. CRITICAL FIX: Save the intended destination before wiping it.
        # This is required for "Iterative Gate Alerts". When the gate opens,
        # we will read these values to calculate the path to the NEXT gate/target.
        if army.destination_x is not None and army.destination_y is not None:
            army.original_destination_x = army.destination_x
            army.original_destination_y = army.destination_y

        # 3. Halt the Army
        army.status = "IDLE"

        # Revoke the Celery movement task so it doesn't trigger "arrival" later
        if army.task_id:
            AsyncResult(army.task_id, app=celery_app).revoke(terminate=True)

        # Clear active movement data
        army.destination_x = None
        army.destination_y = None
        army.arrival_time = None
        army.departure_time = None
        army.task_id = None

        # 4. Find Defender's Locked Quarters for Notification
        defender = (
            session.query(User.discord_id, GamePlayer.private_channel_id, House.name)
            .join(GamePlayer, User.user_id == GamePlayer.user_id)
            .join(House, House.house_id == GamePlayer.claimed_house_id)
            .where(
                GamePlayer.claimed_house_id == gate_owner_house_id,
                GamePlayer.game_id == game_id,
                GamePlayer.is_primary == True,  # Ensure we target the main player
            )
            .first()
        )

        # 5. Dispatch Event to Redis (Cog will handle the Discord Embed)
        payload = {
            "type": "GATE_ALERT",
            "guild_id": army.house.game.guild_id,
            "attacking_army_id": army.army_id,
            "gate_name": gate_name,
            "marcher": {
                "house_name": army.house.name,
                "commander": army.commander_name,
                "troops": army.troop_count,
            },
            "defender": {
                "house_id": gate_owner_house_id,
                "house_name": defender[2] if defender else "NPC",
                "discord_id": defender[0] if defender else None,
                "private_channel_id": defender[1] if defender else None,
                "is_npc": defender is None,
            },
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
        session.commit()

    except Exception as e:
        session.rollback()
        print(f"[ERROR] dispatch_gate_alert failed: {e}")
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


from sqlalchemy import or_


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
    This version is IDEMPOTENT using database locks to prevent duplicate interactions.
    """
    session = get_sync_session()
    try:
        # Start a transaction to ensure our lock is held until we are done.
        session.begin()

        # =============================================================
        # THE FIX: Acquire an exclusive lock on both armies involved.
        # We sort by ID to always lock in the same order, preventing deadlocks.
        # Any duplicate task instance will be blocked here until this transaction completes.
        # =============================================================
        id1, id2 = sorted([army1_id, army2_id])

        army1_locked = (
            session.query(Army).filter(Army.army_id == id1).with_for_update().first()
        )
        army2_locked = (
            session.query(Army).filter(Army.army_id == id2).with_for_update().first()
        )

        # We now have exclusive locks. We can safely perform our checks.

        # 1. Check if armies still exist and are in a valid state
        valid_statuses = ["MARCHING", "SAILING", "DOCKED", "IDLE"]
        if (
            not army1_locked
            or not army2_locked
            or army1_locked.status not in valid_statuses
        ):
            print(
                f"Interaction cancelled: Army {army1_id} or {army2_id} invalid or has invalid status."
            )
            session.commit()  # Commit to release the locks
            session.close()
            return

        # 2. Race-Condition-Proof Duplicate Check
        # Because we have the locks, this check is now guaranteed to be accurate.
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
            session.commit()  # Commit to release the locks
            session.close()
            return

        # 3. Calculate Expiry with SAFETY BUFFER (Your existing logic is correct)
        now = datetime.datetime.now(datetime.timezone.utc)
        calculated_expiry = intercept_time - datetime.timedelta(seconds=1)
        min_expiry = now + datetime.timedelta(minutes=2)
        final_expiry = max(calculated_expiry, min_expiry)

        # 4. Create the Database Record
        new_interaction = PendingInteraction(
            game_id=game_id,
            army1_id=army1_id,
            army2_id=army2_id,
            status="PENDING",
            expires_at=final_expiry,
            location_x=intercept_x,
            location_y=intercept_y,
        )
        session.add(new_interaction)
        session.flush()

        # 5. Schedule the resolver task
        resolver_task = resolve_player_interaction.apply_async(
            args=[new_interaction.id], eta=final_expiry
        )
        new_interaction.resolver_task_id = resolver_task.id

        # 6. Publish to Redis (This will now only happen ONCE)
        payload = {"type": "PROMPT_INTERACTION", "interaction_id": new_interaction.id}
        if REDIS_CLIENT:
            REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

        print(
            f"DEBUG: Created Interaction {new_interaction.id}. Expires in {(final_expiry - now).total_seconds():.1f}s"
        )

        # All operations were successful. Commit the transaction to save the new
        # interaction and release the locks on the armies.
        session.commit()

    except Exception as e:
        print(f"❌ Error in initiate_player_interaction: {e}")
        session.rollback()  # Rollback on any error to release locks and undo changes.
    finally:
        session.close()  # Always close the session.


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
