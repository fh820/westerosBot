import json


def update_coords_from_file(master_path, updates_path):
    """
    Loads a master JSON file and an updates file, applies the new
    coordinates, and saves the master file.
    """
    try:
        # Load the master data file
        with open(master_path, "r") as f:
            master_data = json.load(f)

        # Load the updates file
        with open(updates_path, "r") as f:
            update_data = json.load(f)

    except FileNotFoundError as e:
        print(f"Error: File not found - {e.filename}")
        return
    except json.JSONDecodeError as e:
        print(f"Error: Could not decode JSON from file. Details: {e}")
        return

    # Create a simple lookup map from the update data for efficiency
    # The key is a tuple of (house_name, castle_name)
    updates_map = {
        (update["house"], update["castle"]): {"x": update["x"], "y": update["y"]}
        for region in update_data.values()
        for update in region
    }

    updated_count = 0
    unmatched_houses = list(updates_map.keys())

    # Iterate through the master list and update entries
    for house_entry in master_data:
        key = (house_entry.get("house"), house_entry.get("castle"))
        if key in updates_map:
            house_entry["x"] = updates_map[key]["x"]
            house_entry["y"] = updates_map[key]["y"]
            print(
                f"Updated: {key[0]} ({key[1]}) -> (x: {house_entry['x']}, y: {house_entry['y']})"
            )
            updated_count += 1
            if key in unmatched_houses:
                unmatched_houses.remove(key)  # Remove from list of unmatched

    print(f"\nSuccessfully updated coordinates for {updated_count} houses.")

    if unmatched_houses:
        print(
            "\nWarning: The following houses from the update file were not found in the master file:"
        )
        for house, castle in unmatched_houses:
            print(f"- {house} ({castle})")

    # Save the updated data back to the master file
    with open(master_path, "w") as f:
        json.dump(master_data, f, indent=4)

    print(f"\nChanges have been saved to '{master_path}'.")


# --- SCRIPT EXECUTION ---
if __name__ == "__main__":
    MASTER_FILE = "master_world_data.json"
    UPDATE_FILE = "beyond_update.json"
    update_coords_from_file(MASTER_FILE, UPDATE_FILE)
