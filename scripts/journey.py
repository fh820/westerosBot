import asyncio
import os
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- Setup Environment and Imports ---
load_dotenv()

from app.services.pathfinder_bot_engine import Pathfinder
from app.services.travel_calculator import calculate_travel_duration, format_duration
from app.services.common import slugify  # Import the slugify function

# Assume a default fleet size for the calculation.
DEFAULT_FLEET_SIZE = 50


# --- Main Asynchronous Function ---
async def calculate_sea_journey(start_fief: str, end_fief: str):
    """
    Initializes the pathfinder, calculates a sea journey, and prints the result.
    """
    print("--- Journey Time Calculator ---")

    # 1. Initialize the Pathfinder Engine
    print("Initializing Pathfinder Engine...")
    pf_engine = Pathfinder(
        data_file=str(ROOT / "master_world_data.json"),
        cost_map_file=str(ROOT / "data" / "maps" / "master_coastal_map.png"),
        map_file=str(ROOT / "data" / "maps" / "map.jpg"),
    )
    print("✅ Pathfinder Ready.")

    # =============================================================
    # THE FIX IS HERE: Load JSON manually to find locations
    # =============================================================
    # 2. Find the coordinates for the start and end locations
    print(f"Finding coordinates for '{start_fief}' and '{end_fief}'...")

    try:
        with open(ROOT / "master_world_data.json", "r", encoding="utf-8") as f:
            world_data = json.load(f)
    except FileNotFoundError:
        print("❌ Error: 'master_world_data.json' not found in the root directory.")
        return

    start_loc_data = None
    end_loc_data = None

    # Use slugify for a case-insensitive and robust search, just like your bot does
    slug_start = slugify(start_fief)
    slug_end = slugify(end_fief)

    for location in world_data:
        if slugify(location.get("castle", "")) == slug_start:
            start_loc_data = location
        if slugify(location.get("castle", "")) == slug_end:
            end_loc_data = location
        # Stop searching if we've found both
        if start_loc_data and end_loc_data:
            break

    if not start_loc_data:
        print(f"❌ Error: Could not find location data for '{start_fief}'.")
        return

    if not end_loc_data:
        print(f"❌ Error: Could not find location data for '{end_fief}'.")
        return

    start_coords = (start_loc_data["x"], start_loc_data["y"])
    end_coords = (end_loc_data["x"], end_loc_data["y"])

    print(f"  -> {start_fief}: {start_coords}")
    print(f"  -> {end_fief}: {end_coords}")
    # =============================================================

    # 3. Calculate the path using the 'sea_only' travel mode
    print("\nCalculating sea route...")
    path_data = await pf_engine.find_journey_async(
        start_loc=start_coords,
        end_loc=end_coords,
        travel_mode="sea_only",
    )

    if not path_data:
        print(
            "❌ Error: No viable sea path could be calculated between these two points."
        )
        return

    print("✅ Path calculated successfully.")

    # 4. Calculate the travel duration
    duration_seconds = calculate_travel_duration(
        path_data["terrain_breakdown"],
        army_size=DEFAULT_FLEET_SIZE,
    )

    # 5. Format and print the final results
    print("\n--- JOURNEY REPORT ---")
    print(f"Route:       {start_fief} to {end_fief}")
    print(f"Travel Mode: Sea Only")
    print(f"Distance:    ~{int(path_data.get('total_distance', 0))} units")
    print(f"ETA:         {format_duration(duration_seconds)}")
    print("----------------------")


# --- Script Execution ---
if __name__ == "__main__":
    start_location = "King's Landing"
    end_location = "Braavos"

    asyncio.run(calculate_sea_journey(start_location, end_location))
