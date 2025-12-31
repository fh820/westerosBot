import json
import numpy as np
import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from skimage.graph import MCP_Geometric
import os
import time

# --- CONFIGURATION ---
# DATA_FILE = r"C:\Users\farha\Desktop\WesterosBot\master_world_data.json"
# COST_MAP_FILE = "data/maps/master_coastal_map.png"
# MAP_FILE = "data/maps/map.jpg"
# OUTPUT_DIR = "data/generated_maps"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # project root

DATA_FILE = os.path.join(BASE_DIR, "master_world_data.json")
COST_MAP_FILE = os.path.join(BASE_DIR, "data", "maps", "master_coastal_map.png")
MAP_FILE = os.path.join(BASE_DIR, "data", "maps", "map.jpg")
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "generated_maps")


# --- GRAYSCALE CODES (Must match the generator) ---
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

        # --- 1. PRE-LOAD ALL DATA ON STARTUP ---
        with open(data_file, "r") as f:
            self.data = json.load(f)

        self.cost_map = cv2.imread(cost_map_file, 0)
        self.map_visual = cv2.imread(map_file)
        self.map_visual_rgb = cv2.cvtColor(self.map_visual, cv2.COLOR_BGR2RGB)

        # --- 2. CACHE ---
        self.cache = {}

        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

        print("Engine Ready.")

    # def _get_location(self, identifier):
    #     if isinstance(identifier, (tuple, list)):
    #         return {
    #             "x": int(identifier[0]),
    #             "y": int(identifier[1]),
    #             "castle": f"Custom_{identifier[0]}_{identifier[1]}",
    #         }
    #     return next((d for d in self.data if d["castle"] == identifier), None)

    # def _get_location(self, identifier):
    #     # 1. Handle Tuple/List inputs (e.g., passed directly from code)
    #     if isinstance(identifier, (tuple, list)):
    #         return {
    #             "x": int(identifier[0]),
    #             "y": int(identifier[1]),
    #             "castle": f"Custom_{identifier[0]}_{identifier[1]}",
    #         }

    #     # 2. Handle String inputs (e.g., from Discord)
    #     if isinstance(identifier, str):
    #         # Clean up input
    #         identifier = identifier.strip()

    #         # A. Check if it looks like coordinates (e.g., "1500, 200")
    #         if "," in identifier:
    #             try:
    #                 parts = identifier.split(",")
    #                 if len(parts) == 2:
    #                     x = int(parts[0].strip())
    #                     y = int(parts[1].strip())
    #                     return {"x": x, "y": y, "castle": f"Custom_{x}_{y}"}
    #             except ValueError:
    #                 # If parsing fails (e.g. "King's Landing, The"), ignore and try name lookup
    #                 pass

    #         # B. Look up location name in JSON (Case-insensitive)
    #         # This handles "Winterfell", "winterfell", etc.
    #         return next(
    #             (d for d in self.data if d["castle"].lower() == identifier.lower()),
    #             None,
    #         )

    #     return None

    def _normalize(self, text):
        """
        Helper: Strips spaces, underscores, apostrophes for comparison.
        "King's Landing" -> "kingslanding"
        "Kings_Landing"  -> "kingslanding"
        """
        return (
            text.lower()
            .replace(" ", "")
            .replace("_", "")
            .replace("'", "")
            .replace("-", "")
        )

    def _get_location(self, identifier):
        # 1. Handle Tuple/List inputs (Internal Logic)
        if isinstance(identifier, (tuple, list)):
            return {
                "x": int(identifier[0]),
                "y": int(identifier[1]),
                "castle": f"Custom_{identifier[0]}_{identifier[1]}",
            }

        # 2. Handle String inputs (Discord Input)
        if isinstance(identifier, str):
            identifier = identifier.strip()

            # A. Check for Coordinates "1500, 200"
            if "," in identifier:
                try:
                    parts = identifier.split(",")
                    if len(parts) == 2:
                        x = int(parts[0].strip())
                        y = int(parts[1].strip())
                        return {"x": x, "y": y, "castle": f"Custom_{x}_{y}"}
                except ValueError:
                    pass

            # B. FUZZY MATCHING (The Fix)
            # We normalize the Input ("Kings_Landing" -> "kingslanding")
            target_slug = self._normalize(identifier)

            # We normalize every Castle in JSON ("King's Landing" -> "kingslanding")
            for d in self.data:
                if self._normalize(d["castle"]) == target_slug:
                    return d

        return None

    def _has_sea_access(self, node, radius=50):
        y, x = node["y"], node["x"]
        y_min, y_max = max(0, y - radius), min(self.cost_map.shape[0], y + radius)
        x_min, x_max = max(0, x - radius), min(self.cost_map.shape[1], x + radius)
        area = self.cost_map[y_min:y_max, x_min:x_max]
        if np.any(
            (area == COSTS["ocean"])
            | (area == COSTS["coastal_water"])
            | (area == COSTS["port"])
        ):
            return True
        return False

    def _build_cost_grid(self, gm_settings, travel_mode):
        grid = self.cost_map.astype(np.float64)
        final_grid = np.full(grid.shape, 10000.0, dtype=np.float64)

        final_grid[grid == COSTS["land"]] = 10.0
        final_grid[grid == COSTS["road"]] = 1.0
        final_grid[grid == COSTS["ocean"]] = 5.0
        final_grid[grid == COSTS["coastal_water"]] = 3.0
        final_grid[grid == COSTS["port"]] = 0.5

        if not gm_settings.get("rivers_impassable", True):
            final_grid[grid == COSTS["river_wall"]] = 50.0

        for name in ["twins", "rubyford", "bitterbridge"]:
            if gm_settings.get(f"{name}_open", True):
                final_grid[grid == COSTS[name]] = 2.0

        if travel_mode == "land_only":
            final_grid[grid == COSTS["ocean"]] = 10000.0
            final_grid[grid == COSTS["coastal_water"]] = 10000.0
        elif travel_mode == "sea_only":
            final_grid[grid == COSTS["land"]] = 10000.0
            final_grid[grid == COSTS["road"]] = 10000.0
            for name in ["twins", "rubyford", "bitterbridge"]:
                final_grid[grid == COSTS[name]] = 10000.0
        return final_grid

    def find_journey(
        self, start_loc, end_loc, waypoints=[], gm_settings={}, travel_mode="optimal"
    ):
        start_time = time.perf_counter()

        # --- 1. CACHING & SETUP ---
        # Create a unique key for this specific request
        gm_tuple = tuple(sorted(gm_settings.items()))
        cache_key = (
            str(start_loc),
            str(end_loc),
            tuple(waypoints),
            gm_tuple,
            travel_mode,
        )

        if cache_key in self.cache:
            print("Found route in cache! Returning instantly.")
            return self.cache[cache_key]

        # --- 2. LOCATION VALIDATION ---
        all_stops = [self._get_location(s) for s in [start_loc] + waypoints + [end_loc]]
        if any(s is None for s in all_stops):
            print(f"Error: One or more locations could not be found.")
            return None

        start_node = all_stops[0]

        # Smart Error: Check if trying to sail from a landlocked castle
        if travel_mode == "sea_only" and not self._has_sea_access(start_node):
            print(
                f"--- JOURNEY FAILED: Cannot start 'sea_only' from '{start_node['castle']}'. No sea access. ---"
            )
            return None

        # --- 3. PATH CALCULATION & STATS ---
        final_cost_grid = self._build_cost_grid(gm_settings, travel_mode)
        full_path_x, full_path_y = [], []

        # Stats containers
        terrain_breakdown = {"road": 0, "land": 0, "sea": 0}
        total_pixel_distance = 0.0

        print("Calculating journey...")
        try:
            for i in range(len(all_stops) - 1):
                leg_start, leg_end = all_stops[i], all_stops[i + 1]

                # Optimization Scale (Matches instructions)
                SCALE = 0.20

                # Resize grid for speed
                small_grid = cv2.resize(
                    final_cost_grid,
                    (0, 0),
                    fx=SCALE,
                    fy=SCALE,
                    interpolation=cv2.INTER_NEAREST,
                )

                # Calculate scaled coordinates
                sy, sx = int(leg_start["y"] * SCALE), int(leg_start["x"] * SCALE)
                ey, ex = int(leg_end["y"] * SCALE), int(leg_end["x"] * SCALE)

                # Safety bounds
                sy = min(max(sy, 0), small_grid.shape[0] - 1)
                sx = min(max(sx, 0), small_grid.shape[1] - 1)
                ey = min(max(ey, 0), small_grid.shape[0] - 1)
                ex = min(max(ex, 0), small_grid.shape[1] - 1)

                # Clear landing zones (low cost)
                small_grid[max(0, sy - 2) : sy + 3, max(0, sx - 2) : sx + 3] = 0.1
                small_grid[max(0, ey - 2) : ey + 3, max(0, ex - 2) : ex + 3] = 0.1

                # Run MCP
                mcp = MCP_Geometric(small_grid)
                _, traceback_obj = mcp.find_costs([(sy, sx)])
                path = mcp.traceback((ey, ex))
                path = np.array(path).T

                # Scale coordinates back to original size
                segment_x = path[1] / SCALE
                segment_y = path[0] / SCALE

                full_path_x.extend(segment_x)
                full_path_y.extend(segment_y)

                # Calculate Distance and Terrain stats for this leg
                for j in range(len(segment_x) - 1):
                    # Euclidean distance
                    dist = np.sqrt(
                        (segment_x[j + 1] - segment_x[j]) ** 2
                        + (segment_y[j + 1] - segment_y[j]) ** 2
                    )
                    total_pixel_distance += dist

                    # Check terrain type
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
            print(f"FAILURE: No path could be found. Error: {e}")
            return None

        # --- 4. VISUALIZATION ---
        print("Generating multi-color route...")
        fig, ax = plt.subplots(figsize=(20, 15))
        ax.imshow(self.map_visual_rgb)

        # Color path segments
        path_segments, current_segment, current_terrain = [], [], None

        for i in range(len(full_path_x)):
            x, y = int(full_path_x[i]), int(full_path_y[i])
            if (
                y >= self.cost_map.shape[0]
                or x >= self.cost_map.shape[1]
                or y < 0
                or x < 0
            ):
                continue

            code = self.cost_map[y, x]
            # Determine if this pixel is Sea or Land for coloring
            # NOTE: Ports count as SEA for visualization too!
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

        # Draw lines
        for seg in path_segments:
            if len(seg["p"]) < 2:
                continue
            px, py = zip(*seg["p"])
            color = "blue" if seg["t"] == "sea" else "red"
            ax.plot(px, py, color=color, linewidth=1.5, alpha=0.9)

        # Plot Start/End/Waypoints
        ax.plot(
            all_stops[0]["x"],
            all_stops[0]["y"],
            "go",
            markersize=8,
            markeredgecolor="black",
            label="Start",
        )
        ax.plot(
            all_stops[-1]["x"],
            all_stops[-1]["y"],
            "o",
            color="purple",
            markersize=8,
            markeredgecolor="black",
            label="End",
        )
        for wp in all_stops[1:-1]:
            ax.plot(wp["x"], wp["y"], "yo", markersize=7, markeredgecolor="black")

        title = f"Journey: {start_loc} to {end_loc} (Mode: {travel_mode})"
        ax.set_title(title, fontsize=16)
        ax.axis("off")
        plt.tight_layout()

        # Save Image
        filename = f"{OUTPUT_DIR}/{title.replace(' ', '_').replace(':', '')}_{hash(cache_key)}.png"
        plt.savefig(filename, dpi=150)
        plt.close(fig)  # Free memory

        end_time = time.perf_counter()
        print(f"Finished in {end_time - start_time:.4f} seconds.")

        # --- 5. RESULT PREPARATION ---
        # Convert Numpy types to Python floats for JSON safety
        result_data = {
            "image": filename,
            "total_distance": float(total_pixel_distance),
            "terrain_breakdown": {
                "road": float(terrain_breakdown["road"]),
                "land": float(terrain_breakdown["land"]),
                "sea": float(terrain_breakdown["sea"]),
            },
            "start_point": start_loc,
            "end_point": end_loc,
        }

        # Update Cache with the FULL data, not just the filename
        self.cache[cache_key] = result_data

        return result_data


# --- HOW TO USE IN YOUR BOT ---
if __name__ == "__main__":

    # 1. On bot startup, create ONE Pathfinder instance.
    # This is the slow part (~2 seconds).
    pathfinder = Pathfinder(
        data_file=DATA_FILE, cost_map_file=COST_MAP_FILE, map_file=MAP_FILE
    )

    # 2. When a user runs a command, call the `find_journey` method.
    # print("\n--- User 1 requests a complex journey ---")
    # # First time will be slow (~1-2 seconds)
    # pathfinder.find_journey(
    #     start_loc="Winterfell",
    #     end_loc="Sunspear",
    #     waypoints=["King's Landing"],
    #     travel_mode="optimal",
    # )

    # print("\n--- User 2 requests the SAME journey ---")
    # # Second time will be almost instantaneous (< 0.01 seconds)
    # pathfinder.find_journey(
    #     start_loc="Winterfell",
    #     end_loc="Sunspear",
    #     waypoints=["King's Landing"],
    #     travel_mode="optimal",
    # )

    print("\n--- User 3 requests a different journey ---")
    # This will be calculated fresh.
    pathfinder.find_journey(
        start_loc="601, 742",
        end_loc="940, 1863",
        travel_mode="land_only",
        gm_settings={"twins_open": False},  # Frey is angry
    )
