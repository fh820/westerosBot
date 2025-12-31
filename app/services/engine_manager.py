# app/services/engine_manager.py

import os
from app.services.pathfinder_bot_engine import Pathfinder

# Initialize Engine Once, in a central, neutral location.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

PF_ENGINE = Pathfinder(
    data_file=os.path.join(BASE_DIR, "master_world_data.json"),
    cost_map_file=os.path.join(BASE_DIR, "data", "maps", "master_coastal_map.png"),
    map_file=os.path.join(BASE_DIR, "data", "maps", "map.jpg"),
)
