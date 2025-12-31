from app.celery_app import celery_app
from app.db.sync_db import get_sync_session
from app.db.models import Army, House, GamePlayer, User, Fief, Game
from app.db.repositories import ArmyRepo
from app.services.pathfinder_bot_engine import Pathfinder
from app.services.travel_calculator import calculate_travel_duration, format_duration
import json
import redis
import os
import datetime


# Initialize Pathfinder in the Worker Process
PF_ENGINE = Pathfinder(
    data_file="master_world_data.json",
    cost_map_file="data/maps/master_coastal_map.png",
    map_file="data/maps/map.jpg",
)

REDIS_CLIENT = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


@celery_app.task
def process_banner_call(
    game_id: int,
    rally_point: str,
    liege_house_id: int,
    user_discord_id: int,
    vassal_instructions: list,
):
    """
    Worker task to process mass troop movements, now resilient to invalid data.
    """
    print(f"⚡ Celery: Processing Banner Call for House ID {liege_house_id}...")
    session = get_sync_session()

    report_lines = []
    max_duration = 0
    total_raised = 0

    try:
        # 1. Get Context Data
        dest_fief = (
            session.query(Fief)
            .filter(Fief.game_id == game_id, Fief.name.ilike(rally_point))
            .first()
        )
        liege_house = (
            session.query(House).filter(House.house_id == liege_house_id).first()
        )

        if not dest_fief or not liege_house:
            print(
                f"❌ Banner Task Error: Could not find destination fief '{rally_point}' or liege house ID {liege_house_id}."
            )
            return

        end_c = (dest_fief.location_x, dest_fief.location_y)

        # 2. Loop through vassal instructions
        for instr in vassal_instructions:
            h_id = instr["house_id"]
            percent = instr["percent"]

            house = session.query(House).filter(House.house_id == h_id).first()
            if not house or percent <= 0:
                continue

            garrisons = (
                session.query(Army)
                .filter(
                    Army.house_id == h_id,
                    Army.status.in_(["GARRISONED", "IDLE"]),
                    Army.troop_count > 0,
                )
                .all()
            )

            moved_count = 0

            # Loop through each garrison belonging to the vassal
            for garrison in garrisons:
                amount = int(garrison.troop_count * percent)
                if amount < 10:
                    continue

                start_c = (garrison.location_x, garrison.location_y)

                # --- FIX START: Add validation before calling the pathfinder ---

                # CHECK 1: Skip garrisons with invalid (0,0) coordinates.
                if start_c == (0, 0):
                    print(
                        f"⚠️ SKIPPING Garrison ID {garrison.army_id} from {house.name}: Invalid coordinates (0,0)."
                    )
                    continue  # This is crucial: it skips to the next garrison in the loop.

                # Pathfinding (Sync)
                path = PF_ENGINE._find_journey_sync(
                    start_loc=start_c, end_loc=end_c, travel_mode="optimal"
                )

                # CHECK 2: Skip garrisons where no valid path can be found.
                if not path:
                    fief_name = (
                        garrison.fief.name if garrison.fief else f"coords {start_c}"
                    )
                    print(
                        f"⚠️ SKIPPING Garrison ID {garrison.army_id} from {fief_name}: No path found to {rally_point}."
                    )
                    continue  # Also skips to the next garrison.

                # --- FIX END ---

                # This code will now ONLY run if the garrison has valid coords AND a valid path.
                dur = calculate_travel_duration(path["terrain_breakdown"], amount)
                if dur > max_duration:
                    max_duration = dur

                arrival = datetime.datetime.now(
                    datetime.timezone.utc
                ) + datetime.timedelta(seconds=dur)

                # Split composition correctly
                ratio = amount / garrison.troop_count
                levy_comp = {}
                source_comp = dict(garrison.composition)
                for u, c in source_comp.items():
                    moving = int(c * ratio)
                    levy_comp[u] = moving
                    source_comp[u] -= moving
                garrison.composition = source_comp
                garrison.troop_count -= amount

                # Create the new marching army
                new_levy = Army(
                    game_id=game_id,
                    house_id=liege_house_id,  # The levy now belongs to the liege
                    commander_name=f"{house.name} Levy",
                    troop_count=amount,
                    composition=levy_comp,
                    location_x=start_c[0],
                    location_y=start_c[1],
                    status="MARCHING",
                    destination_x=end_c[0],
                    destination_y=end_c[1],
                    arrival_time=arrival,
                )
                session.add(new_levy)
                moved_count += amount

            # This part of the logic remains the same
            if moved_count > 0:
                report_lines.append(
                    f"✅ **{house.name}** marches with {moved_count} men."
                )
                total_raised += moved_count
            else:
                # This message will now correctly appear if ALL of a vassal's garrisons
                # had invalid coords or no path.
                report_lines.append(
                    f"⚠️ **{house.name}** could not muster forces (no valid routes or garrisons)."
                )

        session.commit()

        # 3. Publish Result to Redis
        payload = {
            "type": "BANNER_REPORT",
            "guild_id": dest_fief.game.guild_id,
            "owner_id": user_discord_id,
            "liege_house_name": liege_house.name,
            "report_lines": report_lines,
            "total_raised": total_raised,
            "max_duration": format_duration(max_duration),
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

    except Exception as e:
        print(f"❌ Banner Task Error: {e}")
        session.rollback()
    finally:
        session.close()
