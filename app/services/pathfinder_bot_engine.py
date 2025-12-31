# app/services/pathfinder_bot_engine.py

import json
import numpy as np
import cv2
import os
import time
import asyncio
import io
import hashlib
import pickle
import redis

# --- MATPLOTLIB (THREAD-SAFE SETUP) ---
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from skimage.graph import MCP_Geometric

# --- CONFIGURATION ---
DATA_FILE = "master_world_data.json"
COST_MAP_FILE = "data/maps/master_coastal_map.png"
MAP_FILE = "data/maps/map.jpg"
OUTPUT_DIR = "data/generated_maps"

# --- COST CODES ---f
COSTS = {
    "land": 50,
    "ocean": 150,
    "road": 20,
    "port": 2,
    "coastal_water": 80,
    "twins": 5,
    "rubyford": 6,
    "bitterbridge": 7,
    "river_wall": 255,
}


class Pathfinder:
    def __init__(self, data_file, cost_map_file, map_file):
        print("Redis Initializing...")
        # Initialize Redis
        try:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            # 2. Convert string -> Connection Object
            self.redis = redis.from_url(redis_url)
            print("Pathfinder connected to Redis.")
        except Exception as e:
            print(f"Warning: Redis connection failed. Caching disabled. {e}")
            self.redis = None

        print("Pathfinder Engine Initializing...")
        with open(data_file, "r") as f:
            self.data = json.load(f)

        self.cost_map = cv2.imread(cost_map_file, 0)
        self.map_visual = cv2.imread(map_file)
        self.map_visual_rgb = cv2.cvtColor(self.map_visual, cv2.COLOR_BGR2RGB)
        self.cache = {}

        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)
        print("Engine Ready.")

    async def find_journey_async(self, *args, **kwargs):
        return await asyncio.to_thread(self._find_journey_sync, *args, **kwargs)

    def _normalize(self, text):
        return (
            text.lower()
            .replace(" ", "")
            .replace("_", "")
            .replace("'", "")
            .replace("-", "")
        )

    def _get_location(self, identifier):
        if isinstance(identifier, (tuple, list)):
            return {"x": int(identifier[0]), "y": int(identifier[1])}
        if isinstance(identifier, str):
            identifier = identifier.strip()
            if "," in identifier:
                try:
                    x, y = map(int, identifier.split(","))
                    return {"x": x, "y": y}
                except ValueError:
                    pass
            target_slug = self._normalize(identifier)
            for d in self.data:
                if self._normalize(d["castle"]) == target_slug:
                    return d
        return None

    def _generate_cache_key(
        self, start_loc, end_loc, waypoints, gm_settings, travel_mode
    ):
        """Generates a unique hash based on trip parameters and game settings."""
        payload = {
            "s": start_loc,
            "e": end_loc,
            "w": waypoints,
            "m": travel_mode,
            "cfg": gm_settings,  # Crucial: if settings change (bridges close), key changes
        }
        # Create a consistent string and hash it
        payload_str = json.dumps(payload, sort_keys=True, default=str)
        return f"pathfinder:{hashlib.md5(payload_str.encode()).hexdigest()}"

    def _build_cost_grid(self, gm_settings, travel_mode):
        grid = self.cost_map.astype(np.float64)
        final_grid = np.full(grid.shape, 10000.0, dtype=np.float64)

        # --- PRESET MODES ---
        if travel_mode == "optimal":
            # Hybrid mode: Land and Sea are roughly equal
            final_grid[grid == COSTS["land"]] = 5.0
            final_grid[grid == COSTS["road"]] = 1.0
            final_grid[grid == COSTS["ocean"]] = 5.0
            final_grid[grid == COSTS["coastal_water"]] = 5.0
            final_grid[grid == COSTS["port"]] = 1.0
            # Allow river crossing (ferries)
            if not gm_settings.get("rivers_impassable", True):
                final_grid[grid == COSTS["river_wall"]] = 50.0
            else:
                final_grid[grid == COSTS["river_wall"]] = 50.0

        elif travel_mode == "land_only":
            final_grid[grid == COSTS["land"]] = 10.0
            final_grid[grid == COSTS["road"]] = 1.0
            # Sea is wall
            final_grid[grid == COSTS["ocean"]] = 10000.0
            final_grid[grid == COSTS["coastal_water"]] = 10000.0
            # River crossing logic
            if not gm_settings.get("rivers_impassable", True):
                final_grid[grid == COSTS["river_wall"]] = 50.0

        elif travel_mode == "sea_only":
            final_grid[grid == COSTS["ocean"]] = 5.0
            final_grid[grid == COSTS["coastal_water"]] = 3.0
            final_grid[grid == COSTS["port"]] = 1.0
            # Land is wall
            final_grid[grid == COSTS["land"]] = 10000.0
            final_grid[grid == COSTS["road"]] = 10000.0

        # --- BRIDGES (Always apply) ---
        for name in ["twins", "rubyford", "bitterbridge"]:
            cost = 1.0 if gm_settings.get(f"{name}_open", True) else 10000.0
            final_grid[grid == COSTS[name]] = cost

        return final_grid

    def _find_journey_sync(
        self, start_loc, end_loc, waypoints=[], gm_settings={}, travel_mode="optimal"
    ):
        """
        Calculates path.
        NEW FEATURE: If travel_mode='hybrid_segment', it forces Sea->Waypoint->Land.
        """
        start_time = time.perf_counter()

        # --- 1. CHECK REDIS CACHE ---
        cache_key = None
        if self.redis:
            try:
                cache_key = self._generate_cache_key(
                    start_loc, end_loc, waypoints, gm_settings, travel_mode
                )
                cached_data = self.redis.get(cache_key)
                if cached_data:
                    # Cache Hit! Deserialize
                    result = pickle.loads(cached_data)
                    # Reconstruct BytesIO object from raw bytes
                    result["image"] = io.BytesIO(result["image_bytes"])
                    return result
            except Exception as e:
                print(f"Redis Read Error: {e}")

        # 1. Resolve All Stops
        all_stops = [self._get_location(s) for s in [start_loc] + waypoints + [end_loc]]
        if any(s is None for s in all_stops):
            return None

        full_path_x, full_path_y = [], []
        terrain_breakdown = {"road": 0.0, "land": 0.0, "sea": 0.0}
        total_pixel_distance = 0.0

        # --- LOGIC BRANCHING ---
        # Define the segments and which mode to use for each leg
        segments = []

        if travel_mode == "hybrid_segment" and len(waypoints) > 0:
            # FORCE LOGIC:
            # 1. Start -> Last Waypoint (SEA ONLY)
            # 2. Last Waypoint -> Destination (LAND ONLY)

            # All stops up to the last waypoint are Sea
            sea_legs = all_stops[:-1]
            # The final leg (Waypoint -> Dest) is Land
            land_leg_start = all_stops[-2]
            land_leg_end = all_stops[-1]

            # Build Sea Segment
            segments.append({"points": sea_legs, "mode": "sea_only"})
            # Build Land Segment
            segments.append(
                {"points": [land_leg_start, land_leg_end], "mode": "land_only"}
            )
        else:
            # Standard behavior: One mode for the whole trip
            segments.append({"points": all_stops, "mode": travel_mode})

        # --- EXECUTE SEGMENTS ---
        try:
            for seg in segments:
                points = seg["points"]
                mode = seg["mode"]

                # Build specific grid for this leg (Sea or Land)
                cost_grid = self._build_cost_grid(gm_settings, mode)

                # Downsample setup
                SCALE = 0.20
                small_grid = cv2.resize(
                    cost_grid,
                    (0, 0),
                    fx=SCALE,
                    fy=SCALE,
                    interpolation=cv2.INTER_NEAREST,
                )

                for i in range(len(points) - 1):
                    p1, p2 = points[i], points[i + 1]

                    sy, sx = int(p1["y"] * SCALE), int(p1["x"] * SCALE)
                    ey, ex = int(p2["y"] * SCALE), int(p2["x"] * SCALE)

                    # Safety: Ensure start/end are walkable on the grid
                    # (Prevents "landing on a cliff" errors)
                    small_grid[max(0, sy - 2) : sy + 3, max(0, sx - 2) : sx + 3] = 0.1
                    small_grid[max(0, ey - 2) : ey + 3, max(0, ex - 2) : ex + 3] = 0.1

                    mcp = MCP_Geometric(small_grid)
                    _, traceback = mcp.find_costs([(sy, sx)])
                    path = mcp.traceback((ey, ex))

                    path = np.array(path).T
                    segment_x, segment_y = path[1] / SCALE, path[0] / SCALE

                    full_path_x.extend(segment_x)
                    full_path_y.extend(segment_y)

                    # Calculate Distance & Terrain Stats
                    for j in range(len(segment_x) - 1):
                        dist = np.sqrt(
                            (segment_x[j + 1] - segment_x[j]) ** 2
                            + (segment_y[j + 1] - segment_y[j]) ** 2
                        )
                        total_pixel_distance += dist

                        cx, cy = int(segment_x[j]), int(segment_y[j])
                        if (
                            0 <= cy < self.cost_map.shape[0]
                            and 0 <= cx < self.cost_map.shape[1]
                        ):
                            val = self.cost_map[cy, cx]
                            if val == COSTS["road"]:
                                terrain_breakdown["road"] += dist
                            elif val in [
                                COSTS["ocean"],
                                COSTS["coastal_water"],
                                COSTS["port"],
                            ]:
                                terrain_breakdown["sea"] += dist
                            else:
                                terrain_breakdown["land"] += dist

        except Exception as e:
            print(f"Pathfinder Failure: {e}")
            return None

        # --- VISUALIZATION (Same as before) ---
        fig = Figure(figsize=(20, 15))
        ax = fig.add_subplot(111)
        ax.axis("off")
        ax.imshow(self.map_visual_rgb)

        path_segments = []
        current_segment = []
        current_terrain = None

        for i in range(len(full_path_x)):
            x, y = int(full_path_x[i]), int(full_path_y[i])
            if not (
                0 <= y < self.cost_map.shape[0] and 0 <= x < self.cost_map.shape[1]
            ):
                continue

            code = self.cost_map[y, x]
            terrain = (
                "sea"
                if code in [COSTS["ocean"], COSTS["coastal_water"], COSTS["port"]]
                else "land"
            )

            if current_terrain is None:
                current_terrain = terrain
            if terrain != current_terrain:
                current_segment.append((x, y))
                path_segments.append({"t": current_terrain, "p": current_segment})
                current_segment = [(x, y)]
                current_terrain = terrain
            else:
                current_segment.append((x, y))

        if current_segment:
            path_segments.append({"t": current_terrain, "p": current_segment})

        for seg in path_segments:
            if len(seg["p"]) < 2:
                continue
            px, py = zip(*seg["p"])
            ax.plot(
                px,
                py,
                color=("blue" if seg["t"] == "sea" else "red"),
                linewidth=1.5,
                alpha=0.9,
            )

        # Draw Markers
        ax.plot(
            all_stops[0]["x"],
            all_stops[0]["y"],
            "go",
            markersize=8,
            markeredgecolor="black",
        )
        ax.plot(
            all_stops[-1]["x"],
            all_stops[-1]["y"],
            "o",
            color="purple",
            markersize=8,
            markeredgecolor="black",
        )
        for wp in all_stops[1:-1]:
            ax.plot(wp["x"], wp["y"], "yo", markersize=7, markeredgecolor="black")

        image_buffer = io.BytesIO()
        canvas = FigureCanvasAgg(fig)
        canvas.print_png(image_buffer)
        image_buffer.seek(0)
        del fig, canvas, ax

        # return {
        #     "image": image_buffer,
        #     "total_distance": float(total_pixel_distance),
        #     "terrain_breakdown": terrain_breakdown,
        #     "path_points": list(zip(full_path_x, full_path_y)),
        # }
        # Extract raw bytes for pickling
        image_bytes = image_buffer.getvalue()

        # Construct result dictionary
        result = {
            "image": image_buffer,
            "image_bytes": image_bytes,  # Needed for serialization
            "total_distance": float(total_pixel_distance),
            "terrain_breakdown": terrain_breakdown,
            "path_points": list(zip(full_path_x, full_path_y)),
        }

        # --- SAVE TO REDIS (24 Hour Expiry) ---
        if self.redis and cache_key:
            try:
                # Store the whole result dict (including image_bytes)
                self.redis.setex(cache_key, 86400, pickle.dumps(result))
            except Exception as e:
                print(f"Redis Write Error: {e}")

        return result
