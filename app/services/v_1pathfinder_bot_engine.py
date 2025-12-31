# app/services/pathfinder_bot_engine.py

import json
import numpy as np
import cv2
import os
import time
import asyncio
import io

# --- MATPLOTLIB (THREAD-SAFE SETUP) ---
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from skimage.graph import MCP_Geometric

# --- CONFIGURATION ---
DATA_FILE = "master_world_data.json"
COST_MAP_FILE = "data/maps/master_coastal_map.png"
MAP_FILE = "data/maps/map.jpg"
OUTPUT_DIR = "data/generated_maps"

# --- COST CODES ---
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
        print("Pathfinder Engine Initializing... (This may take a few seconds)")

        # Load JSON Data
        with open(data_file, "r") as f:
            self.data = json.load(f)

        # Load Maps
        # Cost map is Grayscale (0)
        self.cost_map = cv2.imread(cost_map_file, 0)

        # Visual map is BGR, convert to RGB for Matplotlib once here
        self.map_visual = cv2.imread(map_file)
        self.map_visual_rgb = cv2.cvtColor(self.map_visual, cv2.COLOR_BGR2RGB)

        self.cache = {}

        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

        print("Engine Ready.")

    # --- ASYNC WRAPPER ---
    async def find_journey_async(self, *args, **kwargs):
        """
        Runs the blocking _find_journey_sync method in a separate thread.
        This is crucial to prevent blocking the Discord bot event loop.
        """
        return await asyncio.to_thread(self._find_journey_sync, *args, **kwargs)

    # --- HELPER METHODS ---
    def _normalize(self, text):
        """Strips special characters for fuzzy matching."""
        return (
            text.lower()
            .replace(" ", "")
            .replace("_", "")
            .replace("'", "")
            .replace("-", "")
        )

    def _get_location(self, identifier):
        """Finds a location by name, coordinates string, or tuple."""
        # Handle Tuple/List inputs directly
        if isinstance(identifier, (tuple, list)):
            return {
                "x": int(identifier[0]),
                "y": int(identifier[1]),
                "castle": f"Custom_{int(identifier[0])}_{int(identifier[1])}",
            }

        # Handle String inputs
        if isinstance(identifier, str):
            identifier = identifier.strip()
            # Check for "x,y" format
            if "," in identifier:
                try:
                    x, y = map(int, identifier.split(","))
                    return {"x": x, "y": y, "castle": f"Custom_{x}_{y}"}
                except ValueError:
                    pass

            # Name Lookup
            target_slug = self._normalize(identifier)
            for d in self.data:
                if self._normalize(d["castle"]) == target_slug:
                    return d

        return None

    def _build_cost_grid(self, gm_settings, travel_mode):
        """Creates the movement cost grid based on current settings."""
        grid = self.cost_map.astype(np.float64)

        # Default high cost (Impassable)
        final_grid = np.full(grid.shape, 10000.0, dtype=np.float64)

        # --- MODE 1: OPTIMAL (Hybrid - Used for !sail destination planning) ---
        if travel_mode == "optimal":
            # 1. EQUALIZE COSTS
            # We set Land and Sea to the SAME cost (5.0).
            # This forces the bot to choose the path based on shortest distance.
            final_grid[grid == COSTS["land"]] = 5.0
            final_grid[grid == COSTS["ocean"]] = 5.0
            final_grid[grid == COSTS["coastal_water"]] = 5.0
            
            # 2. PRIORITIZE ROADS
            # Roads remain super cheap (1.0) so armies stick to them when possible.
            final_grid[grid == COSTS["road"]] = 1.0

            # 3. ALLOW RIVER CROSSING (Critical for White Harbor)
            # We assume a fleet/army in "Optimal" mode can use local ferries to cross rivers
            # if necessary to reach a road (like crossing the White Knife).
            final_grid[grid == COSTS["river_wall"]] = 50.0 # High cost, but passable

            # 4. Ports are cheap transition points
            final_grid[grid == COSTS["port"]] = 1.0

        # --- MODE 2: LAND ONLY (Used for !march) ---
        elif travel_mode == "land_only":
            final_grid[grid == COSTS["land"]] = 10.0
            final_grid[grid == COSTS["road"]] = 1.0
            # Water is walls
            final_grid[grid == COSTS["ocean"]] = 10000.0
            final_grid[grid == COSTS["coastal_water"]] = 10000.0
            # Standard river logic
            if not gm_settings.get("rivers_impassable", True):
                final_grid[grid == COSTS["river_wall"]] = 50.0

        # --- MODE 3: SEA ONLY (Used for !sail specific segments) ---
        elif travel_mode == "sea_only":
            final_grid[grid == COSTS["ocean"]] = 5.0
            final_grid[grid == COSTS["coastal_water"]] = 3.0
            # Land is walls
            final_grid[grid == COSTS["land"]] = 10000.0
            final_grid[grid == COSTS["road"]] = 10000.0
            final_grid[grid == COSTS["port"]] = 1.0

        # --- BRIDGES (Apply to all modes) ---
        for name in ["twins", "rubyford", "bitterbridge"]:
            if gm_settings.get(f"{name}_open", True):
                final_grid[grid == COSTS[name]] = 1.0 # Make bridges very attractive
            else:
                final_grid[grid == COSTS[name]] = 10000.0 # Closed bridge = Wall

        return final_grid

    def _find_journey_sync(
        self, start_loc, end_loc, waypoints=[], gm_settings={}, travel_mode="optimal"
    ):
        """
        Calculates the path and draws the map. Returns an in-memory image buffer.
        BLOCKING FUNCTION - Must be run in an executor/thread.
        """
        start_time = time.perf_counter()

        # Check Cache
        gm_tuple = tuple(sorted(gm_settings.items()))
        cache_key = (
            str(start_loc),
            str(end_loc),
            tuple(waypoints),
            gm_tuple,
            travel_mode,
        )
        if cache_key in self.cache:
            # We return a fresh copy of the buffer so the original isn't closed/read
            cached_data = self.cache[cache_key]
            # If we cached an image buffer, we need to seek to 0 before returning
            if isinstance(cached_data.get("image"), io.BytesIO):
                cached_data["image"].seek(0)
            return cached_data

        # Resolve Locations
        all_stops = [self._get_location(s) for s in [start_loc] + waypoints + [end_loc]]
        if any(s is None for s in all_stops):
            return None

        # Build Grid
        final_cost_grid = self._build_cost_grid(gm_settings, travel_mode)

        full_path_x, full_path_y = [], []
        terrain_breakdown = {"road": 0.0, "land": 0.0, "sea": 0.0}
        total_pixel_distance = 0.0

        # --- Pathfinding Loop ---
        try:
            for i in range(len(all_stops) - 1):
                leg_start, leg_end = all_stops[i], all_stops[i + 1]

                # Downsample for speed (Resolution vs Speed trade-off)
                SCALE = 0.20
                small_grid = cv2.resize(
                    final_cost_grid,
                    (0, 0),
                    fx=SCALE,
                    fy=SCALE,
                    interpolation=cv2.INTER_NEAREST,
                )

                sy, sx = int(leg_start["y"] * SCALE), int(leg_start["x"] * SCALE)
                ey, ex = int(leg_end["y"] * SCALE), int(leg_end["x"] * SCALE)

                # Ensure start/end points are walkable in the downsampled grid
                small_grid[max(0, sy - 2) : sy + 3, max(0, sx - 2) : sx + 3] = 0.1
                small_grid[max(0, ey - 2) : ey + 3, max(0, ex - 2) : ex + 3] = 0.1

                # Calculate Path
                mcp = MCP_Geometric(small_grid)
                _, traceback = mcp.find_costs([(sy, sx)])
                path = mcp.traceback((ey, ex))

                # Scale back up
                path = np.array(path).T
                segment_x, segment_y = path[1] / SCALE, path[0] / SCALE

                full_path_x.extend(segment_x)
                full_path_y.extend(segment_y)

                # Calculate Costs based on Full Resolution Map
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
                        ]:
                            terrain_breakdown["sea"] += dist
                        else:
                            terrain_breakdown["land"] += dist

        except Exception as e:
            print(f"Pathfinder Failure: {e}")
            return None

        # --- Visualization (Thread-Safe) ---
        fig = Figure(figsize=(20, 15))
        ax = fig.add_subplot(111)
        ax.axis("off")  # Hide axes

        # Display the RGB map
        ax.imshow(self.map_visual_rgb)

        # Process path segments for coloring
        path_segments = []
        current_segment = []
        current_terrain = None

        for i in range(len(full_path_x)):
            x, y = int(full_path_x[i]), int(full_path_y[i])

            # Boundary check
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

        # Draw Lines
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
        # Start (Green)
        ax.plot(
            all_stops[0]["x"],
            all_stops[0]["y"],
            "go",
            markersize=8,
            markeredgecolor="black",
        )
        # End (Purple)
        ax.plot(
            all_stops[-1]["x"],
            all_stops[-1]["y"],
            "o",
            color="purple",
            markersize=8,
            markeredgecolor="black",
        )
        # Waypoints (Yellow)
        for wp in all_stops[1:-1]:
            ax.plot(wp["x"], wp["y"], "yo", markersize=7, markeredgecolor="black")

        # --- SAVE IMAGE TO MEMORY ---
        image_buffer = io.BytesIO()
        canvas = FigureCanvasAgg(fig)
        canvas.print_png(image_buffer)
        image_buffer.seek(0)

        # Explicit cleanup
        del fig, canvas, ax

        result_data = {
            "image": image_buffer,
            "total_distance": float(total_pixel_distance),
            "terrain_breakdown": terrain_breakdown,
            "path_points": list(zip(full_path_x, full_path_y)),
        }

        # --- SMART CACHING ---
        # Only cache if successful
        self.cache[cache_key] = result_data

        print(f"Pathfinder Finished in {time.perf_counter() - start_time:.4f} seconds.")
        return result_data
