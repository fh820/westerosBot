from app.celery_app import celery_app
from app.services.pathfinder_bot_engine import Pathfinder
from app.services.travel_calculator import calculate_travel_duration, format_duration
import json
import redis
import os

# The worker initializes its own Pathfinder instance in its own process
PF_ENGINE = Pathfinder(
    data_file="master_world_data.json",
    cost_map_file="data/maps/master_coastal_map.png",
    map_file="data/maps/map.jpg",
)

# REDIS_CLIENT = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
REDIS_CLIENT = redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    socket_timeout=1,
    socket_connect_timeout=1,
    retry_on_timeout=True,
)


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
    Celery task to generate a path and publish the result to Redis.
    """
    print(f"Celery Worker: Generating path from {start_loc} to {end_loc}...")
    import os  # Add the import here

    print(f"--- ⚔️  WORKER CWD: {os.getcwd()} ⚔️  ---")  # ADD THIS LINE
    print(f"Celery Worker: Generating path from {start_loc} to {end_loc}...")
    # This is a blocking call, but it's okay because we are in a separate process.
    # We use the renamed _find_journey_sync method
    path_data = PF_ENGINE._find_journey_sync(
        start_loc=start_loc, end_loc=end_loc, travel_mode=travel_mode
    )

    if not path_data:
        # Publish a failure event
        payload = {
            "type": "PATH_FAILED",
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "reason": "No valid path could be found.",
        }
    else:
        # Calculate time and prepare success payload
        duration = calculate_travel_duration(path_data["terrain_breakdown"], army_size)

        payload = {
            "type": "PATH_READY",
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "image_path": path_data["image"],
            "time": format_duration(duration),
            "distance": int(path_data["total_distance"]),
            "origin": str(start_loc),
            "destination": str(end_loc),
            "mode": travel_mode,
        }

    # Publish the result to the 'westeros_bot_events' channel for the bot to pick up
    REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
    print("Celery Worker: Published result to Redis.")
