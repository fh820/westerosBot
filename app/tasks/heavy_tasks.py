# app/tasks/heavy_tasks.py

from app.celery_app import celery_app
from app.db.sync_db import get_sync_session
from app.db.models import Army, House, GamePlayer, User, Fief, Game, MarchLog
from app.services.pathfinder_bot_engine import Pathfinder
from app.services.travel_calculator import calculate_travel_duration, format_duration
import json
import redis
import os
import datetime
from sqlalchemy.orm.attributes import flag_modified

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
    Worker task to process mass troop movements.
    UPDATED: Now generates MarchLogs for interceptions and uses Locked Channel IDs.
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
            return

        # Fetch the Liege's Locked Private Channel for the report
        liege_player = (
            session.query(GamePlayer)
            .filter(
                GamePlayer.game_id == game_id,
                GamePlayer.claimed_house_id == liege_house_id,
                GamePlayer.is_primary == True,
            )
            .first()
        )
        private_channel_id = liege_player.private_channel_id if liege_player else None

        end_c = (dest_fief.location_x, dest_fief.location_y)
        now = datetime.datetime.now(datetime.timezone.utc)

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

            for garrison in garrisons:
                amount = int(garrison.troop_count * percent)
                if amount < 10:
                    continue

                start_c = (garrison.location_x, garrison.location_y)

                if start_c == (0, 0):
                    continue

                # Pathfinding
                path_data = PF_ENGINE._find_journey_sync(
                    start_loc=start_c, end_loc=end_c, travel_mode="optimal"
                )

                if not path_data:
                    continue

                dur = calculate_travel_duration(path_data["terrain_breakdown"], amount)
                if dur > max_duration:
                    max_duration = dur

                arrival = now + datetime.timedelta(seconds=dur)

                # Split composition
                ratio = amount / garrison.troop_count
                levy_comp = {}
                source_comp = dict(garrison.composition)
                for u, c in source_comp.items():
                    moving = int(c * ratio)
                    levy_comp[u] = moving
                    source_comp[u] -= moving

                garrison.composition = source_comp
                garrison.troop_count -= amount
                flag_modified(garrison, "composition")

                # Create the new marching army
                new_levy = Army(
                    game_id=game_id,
                    house_id=liege_house_id,
                    commander_name=f"{house.name} Levy",
                    troop_count=amount,
                    composition=levy_comp,
                    location_x=start_c[0],
                    location_y=start_c[1],
                    status="MARCHING",
                    destination_x=end_c[0],
                    destination_y=end_c[1],
                    departure_time=now,
                    arrival_time=arrival,
                )
                session.add(new_levy)
                session.flush()  # Get new_levy.army_id

                # --- CRITICAL FIX: CREATE MARCH LOGS ---
                # This allows these newly raised levies to be intercepted!
                path_points = path_data.get("path_points", [])
                for pt in path_points:
                    session.add(
                        MarchLog(
                            army_id=new_levy.army_id, game_id=game_id, x=pt[0], y=pt[1]
                        )
                    )

                moved_count += amount

            if moved_count > 0:
                report_lines.append(
                    f"✅ **{house.name}** marches with {moved_count} men."
                )
                total_raised += moved_count
            else:
                report_lines.append(f"⚠️ **{house.name}** could not muster forces.")

        session.commit()

        # 3. Publish Result to Redis
        payload = {
            "type": "BANNER_REPORT",
            "guild_id": dest_fief.game.guild_id,
            "owner_id": user_discord_id,
            "private_channel_id": private_channel_id,  # NEW: Locked ID for delivery
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


@celery_app.task
def generate_path_async(
    guild_id: int,
    channel_id: int,
    user_id: int,
    start_loc,
    end_loc,
    travel_mode,
    army_size,
):
    """
    Celery task to generate a path.
    Payload updated to ensure private_channel_id support.
    """
    path_data = PF_ENGINE._find_journey_sync(
        start_loc=start_loc, end_loc=end_loc, travel_mode=travel_mode
    )

    if not path_data:
        payload = {
            "type": "PATH_FAILED",
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "reason": "No valid path could be found.",
        }
    else:
        duration = calculate_travel_duration(path_data["terrain_breakdown"], army_size)
        payload = {
            "type": "PATH_READY",
            "guild_id": guild_id,
            "channel_id": channel_id,  # Current channel
            "user_id": user_id,
            "image_path": path_data["image"],
            "time": format_duration(duration),
            "distance": int(path_data["total_distance"]),
            "origin": str(start_loc),
            "destination": str(end_loc),
            "mode": travel_mode,
        }

    REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
