# # import eventlet

# # eventlet.monkey_patch()

# import os
# from celery import Celery
# from dotenv import load_dotenv
# from celery.schedules import crontab  # Add this

# load_dotenv()

# REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# celery_app = Celery(
#     "westeros_tasks",
#     broker=REDIS_URL,
#     backend=REDIS_URL,
#     include=[
#         "app.tasks.warfare_tasks",
#         "app.tasks.pathfinding_tasks",
#         "app.tasks.diplomacy_tasks",
#         "app.tasks.logistics_tasks",
#     ],
# )

# celery_app.conf.update(
#     timezone="UTC",
#     enable_utc=True,
#     task_serializer="json",
#     result_serializer="json",
#     accept_content=["json"],
# )


# # SCHEDULE
# celery_app.conf.beat_schedule = {
#     "daily-upkeep-every-24h": {
#         "task": "app.tasks.logistics_tasks.daily_upkeep_tick",
#         "schedule": crontab(hour=0, minute=0),  # Runs at Midnight UTC
#         # For testing, you can change this to: schedule=60.0 (every 60 seconds)
#     },
# }


import os
from celery import Celery
from dotenv import load_dotenv
from celery.schedules import crontab
from kombu import Queue  # <-- IMPORT THIS for defining queues

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "westeros_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL,
    # --- UPDATED: Point to your new, organized task files ---
    include=[
        "app.tasks.light_tasks",
        "app.tasks.heavy_tasks",
    ],
)

# ==============================================================================
# --- NEW: QUEUE AND ROUTING CONFIGURATION ---
# This is where we tell Celery how to separate heavy and light tasks.
# ==============================================================================

# 1. Define the queues that your workers will listen to.
celery_app.conf.task_queues = (
    Queue("light", routing_key="task.light.#"),  # For fast, I/O-bound tasks
    Queue("heavy", routing_key="task.heavy.#"),  # For slow, CPU-bound tasks
)

# 2. Define the rules that send specific tasks to the correct queue.
#    The key is the full path to the task function.
celery_app.conf.task_routes = {
    # --- Light Tasks ---
    "app.tasks.light_tasks.daily_upkeep_tick": {"queue": "light"},
    "app.tasks.light_tasks.process_game_upkeep": {"queue": "light"},
    "app.tasks.light_tasks.resolve_army_arrival": {"queue": "light"},
    "app.tasks.light_tasks.dispatch_scout_report": {"queue": "light"},
    "app.tasks.light_tasks.handle_gate_response": {"queue": "light"},
    "app.tasks.light_tasks.resolve_player_interaction": {"queue": "light"},
    "app.tasks.light_tasks.initiate_player_interaction": {"queue": "light"},
    # --- Heavy Tasks ---
    "app.tasks.heavy_tasks.generate_path_async": {"queue": "heavy"},
    "app.tasks.heavy_tasks.process_banner_call": {"queue": "heavy"},
}

# 3. (Optional but Recommended) Set a default queue for any task not listed above.
celery_app.conf.task_default_queue = "light"


# ==============================================================================
# --- STANDARD CELERY CONFIGURATION (Unchanged) ---
# ==============================================================================
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)


# ==============================================================================
# --- SCHEDULE CONFIGURATION (Updated) ---
# ==============================================================================
celery_app.conf.beat_schedule = {
    "daily-upkeep-every-24h": {
        # --- UPDATED: The path to the task now points to its new location ---
        "task": "app.tasks.light_tasks.daily_upkeep_tick",
        "schedule": crontab(hour=0, minute=0),
    },
}
