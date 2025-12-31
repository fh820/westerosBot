import json

# Define the Choke Points and which regions they guard
# Format: "Castle Name": {"connects": [Region A, Region B]}
CHOKE_POINTS_CONFIG = {
    "The Bloody Gate": {
        "connects": ["The Riverlands", "The Vale"],
        "description": "The only entrance to the Vale from the west.",
    },
    "Moat Cailin": {
        "connects": ["The Riverlands", "The North"],
        "description": "The ancient stronghold guarding the Neck.",
    },
    "Golden Tooth": {
        "connects": ["The Riverlands", "The Westerlands"],
        "description": "The hill fortress guarding the pass to the West.",
    },
    "The Twins": {
        "connects": ["The Riverlands", "The North"],
        "description": "The crossing of the Green Fork.",
    },
    "Prince's Pass": {  # Often held by Fowler
        "connects": ["The Reach", "Dorne"],
        "description": "The primary pass into the Red Mountains.",
    },
    "Vulture's Roost": {  # Alternative pass
        "connects": ["The Stormlands", "Dorne"],
        "description": "A mountain pass into Dorne.",
    },
    "Wyl": {  # The Boneway
        "connects": ["The Stormlands", "Dorne"],
        "description": "The Boneway pass.",
    },
    "Deepwood Motte": {
        "connects": [
            "The North",
            "The Iron Islands",
        ],  # Conceptual connection for naval invasions
        "description": "Key defense for the northern coast.",
    },
}


def mark_chokepoints(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("File not found.")
        return

    count = 0
    for entry in data:
        castle_name = entry.get("castle")

        # Check if this castle is in our config list
        if castle_name in CHOKE_POINTS_CONFIG:
            # Add the choke_point data
            entry["choke_point"] = {
                "is_active": True,
                "connections": CHOKE_POINTS_CONFIG[castle_name]["connects"],
                "desc": CHOKE_POINTS_CONFIG[castle_name]["description"],
            }
            print(f"MARKED: {entry['house']} - {castle_name} as a choke point.")
            count += 1
        else:
            # Ensure other entries don't have leftover data if re-running
            entry["choke_point"] = None

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    print(f"\nSuccess! Marked {count} choke points.")


if __name__ == "__main__":
    mark_chokepoints("master_world_data.json")
