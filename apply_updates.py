import json
import shutil
import os

MASTER_FILE = "master_world_data.json"
UPDATE_FILE = "westerlands_update.json"  # Or whatever you named the file above


def apply_updates():
    # 1. Load the Master Data
    try:
        with open(MASTER_FILE, "r", encoding="utf-8") as f:
            master_data = json.load(f)
    except FileNotFoundError:
        print(f"❌ Error: Could not find {MASTER_FILE}")
        return

    # 2. Load the Updates
    try:
        with open(UPDATE_FILE, "r", encoding="utf-8") as f:
            updates = json.load(f)
    except FileNotFoundError:
        print(f"❌ Error: Could not find {UPDATE_FILE}")
        return

    print(f"🔄 Processing {len(updates)} updates...")

    updates_applied = 0

    # 3. Create a Lookup Dictionary for speed
    # We key by (House, Castle) tuple because House names can duplicate (e.g. Wells)
    # If castle is missing in update, we key by just (House)
    master_index = {}
    for i, entry in enumerate(master_data):
        h = entry.get("house")
        c = entry.get("castle")

        # Index by House+Castle (Most precise)
        if h and c:
            master_index[(h, c)] = i

        # Also Index by House-only (for generic updates)
        # Note: This might get overwritten if multiple castles exist for one house,
        # but usually updates target specific logic.
        if h:
            master_index[(h, None)] = i

    # 4. Apply Updates
    for item in updates:
        target_house = item.get("house")
        target_castle = item.get("castle")  # Optional in update file
        changes = item.get("update")

        if not target_house or not changes:
            print(f"⚠️ Skipping invalid update item: {item}")
            continue

        # Try to find the matching entry index
        target_index = -1

        # Try finding by House + Castle first
        if target_castle:
            target_index = master_index.get((target_house, target_castle), -1)

        # If not found or castle not provided, try finding by just House
        if target_index == -1:
            target_index = master_index.get((target_house, None), -1)

        if target_index != -1:
            # Apply the changes
            entry = master_data[target_index]

            for key, value in changes.items():
                # Handle nested dictionary updates (like army_stats)
                if isinstance(value, dict) and isinstance(entry.get(key), dict):
                    entry[key].update(value)
                else:
                    entry[key] = value

            print(f"✅ Updated {target_house} ({entry.get('castle', 'Unknown')})")
            updates_applied += 1
        else:
            print(
                f"❌ Could not find entry for House: {target_house}, Castle: {target_castle}"
            )

    # 5. Save Data
    if updates_applied > 0:
        # Create Backup
        shutil.copy(MASTER_FILE, f"{MASTER_FILE}.bak")
        print(f"📦 Backup created at {MASTER_FILE}.bak")

        with open(MASTER_FILE, "w", encoding="utf-8") as f:
            json.dump(master_data, f, indent=4)
        print(f"💾 Successfully saved {updates_applied} changes to {MASTER_FILE}")
    else:
        print("No changes were applied.")


if __name__ == "__main__":
    apply_updates()
