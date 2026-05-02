import json
import os
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_missing_coordinates(filename=ROOT / "master_world_data.json"):
    # Check if file exists
    if not os.path.exists(filename):
        print(f"❌ Error: Could not find '{filename}' in the current directory.")
        return

    # Load JSON
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ Error: Failed to parse JSON. {e}")
        return

    # Store found locations grouped by region
    # Structure: { "Region Name": [ "Castle (House)", ... ] }
    zero_coords = defaultdict(list)
    total_count = 0

    print(f"🔎 Scanning {len(data)} entries for (0,0) coordinates...\n")

    for entry in data:
        x = entry.get("x", 0)
        y = entry.get("y", 0)

        # Check for 0,0
        if x == 0 and y == 0:
            region = entry.get("region", "Unknown Region")
            castle = entry.get("castle", "Unknown Castle")
            house = entry.get("house", "Unknown House")

            # Format: "Winterfell (Stark)"
            info_str = f"{castle} ({house})"
            zero_coords[region].append(info_str)
            total_count += 1

    # Print Results
    if total_count == 0:
        print("✅ No locations found with (0,0) coordinates.")
    else:
        print(f"⚠️  Found {total_count} locations with coordinates [0, 0]:\n")

        # Sort regions alphabetically
        for region in sorted(zero_coords.keys()):
            print(f"📍 {region}")
            # Sort locations alphabetically
            for location in sorted(zero_coords[region]):
                print(f"   - {location}")
            print("")


if __name__ == "__main__":
    check_missing_coordinates()
