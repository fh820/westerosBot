import time
import os
import sys

# Ensure we can import from the app directory
sys.path.append(os.getcwd())

from app.services.pathfinder_bot_engine import Pathfinder

# --- CONFIG ---
DATA_FILE = "master_world_data.json"
COST_MAP = "data/maps/master_coastal_map.png"
VISUAL_MAP = "data/maps/map.jpg"


def run_test():
    print("------------------------------------------------")
    print("🚀 STARTING REDIS CACHE TEST")
    print("------------------------------------------------")

    # 1. Initialize Engine
    if not os.path.exists(DATA_FILE):
        print(f"❌ Error: Could not find {DATA_FILE}. Run this from project root.")
        return

    try:
        engine = Pathfinder(DATA_FILE, COST_MAP, VISUAL_MAP)
    except Exception as e:
        print(f"❌ Failed to initialize engine: {e}")
        return

    # Define a heavy route (Long distance)
    start = "Winterfell"
    end = "Kings Landing"

    print(f"\n🗺️  Route: {start} -> {end}")

    # --- RUN 1: COLD START (Calculation) ---
    print("\n🔴 RUN 1: Cold Start (Should be slow)...")
    t0 = time.perf_counter()
    result1 = engine._find_journey_sync(start, end, travel_mode="optimal")
    t1 = time.perf_counter()
    duration1 = t1 - t0

    if result1:
        dist1 = result1["total_distance"]
        print(f"   ✅ Done in {duration1:.4f} seconds")
        print(f"   📏 Distance: {dist1:.2f} pixels")
    else:
        print("   ❌ Failed to find path.")
        return

    # --- RUN 2: CACHED (Fetch) ---
    print("\n🟢 RUN 2: Cached (Should be instant)...")
    t2 = time.perf_counter()
    result2 = engine._find_journey_sync(start, end, travel_mode="optimal")
    t3 = time.perf_counter()
    duration2 = t3 - t2

    if result2:
        dist2 = result2["total_distance"]
        print(f"   ✅ Done in {duration2:.4f} seconds")
        print(f"   📏 Distance: {dist2:.2f} pixels")
    else:
        print("   ❌ Failed to retrieve from cache.")
        return

    # --- RESULTS ---
    print("\n------------------------------------------------")
    print("📊 RESULTS")
    print("------------------------------------------------")

    speedup = duration1 / duration2 if duration2 > 0 else 0

    if duration2 < 0.1:
        print(f"✅ SUCCESS: Cache is working!")
        print(f"🚀 Speed boost: {speedup:.1f}x faster")
    else:
        print(
            f"⚠️ WARNING: Run 2 was slow ({duration2:.4f}s). Redis might not be connected."
        )

    # Validation: Ensure data is identical
    if abs(dist1 - dist2) < 0.01:
        print("✅ Data Integrity: Exact match.")
    else:
        print("❌ Data Integrity: Mismatch (Something is wrong with pickling).")


if __name__ == "__main__":
    run_test()
