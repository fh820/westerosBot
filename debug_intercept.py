# import asyncio
# import os
# import math
# from datetime import datetime
# from sqlalchemy import select, text
# from app.db.db_manager import get_session
# from app.db.models import MarchLog, Army

# # ==========================================
# # CONFIGURATION
# # ==========================================
# # If your DB url is in .env, this should work.
# # Otherwise, paste it here: "postgresql+asyncpg://user:pass@host/dbname"
# DB_URL = os.getenv("DATABASE_URL")

# MOVER_ID = 6639  # Lord Orton
# TARGET_ID = 5006  # Gayjoy

# # Target Location (Where the interception allegedly happened)
# # Gayjoy was at roughly (435, 1564)
# TARGET_X = 435.12
# TARGET_Y = 1564.34
# TARGET_RADIUS = 25.0


# async def analyze_march_logs():
#     print("🕵️  STARTING FORENSIC ANALYSIS 🕵️")

#     async with get_session() as session:
#         # 1. Verify Armies exist
#         mover = await session.get(Army, MOVER_ID)
#         target = await session.get(Army, TARGET_ID)

#         if not mover:
#             print(f"❌ Mover {MOVER_ID} not found.")
#             return

#         print(f"🔹 Mover: {mover.commander_name} (Status: {mover.status})")
#         print(f"🔸 Target: {target.commander_name} (Status: {target.status})")

#         # 2. Pull March Logs for Mover
#         print("\n📜 Fetching March Logs for Orton...")
#         stmt = (
#             select(MarchLog)
#             .where(MarchLog.army_id == MOVER_ID)
#             .order_by(MarchLog.estimated_time)
#         )
#         logs = (await session.execute(stmt)).scalars().all()

#         if not logs:
#             print(
#                 "❌ No march logs found for Orton. The march may have finished or been cancelled."
#             )
#             return

#         print(f"   Found {len(logs)} checkpoints.")

#         # 3. Analyze Logs against Target Position
#         print(
#             f"\n🔍 Searching logs for proximity to Gayjoy ({TARGET_X}, {TARGET_Y})..."
#         )

#         closest_log = None
#         closest_dist = float("inf")

#         closest_time_log = None
#         closest_time_diff = float("inf")

#         # Get Target's current movement details if moving
#         target_logs = []
#         if target.status == "MARCHING":
#             stmt_t = (
#                 select(MarchLog)
#                 .where(MarchLog.army_id == TARGET_ID)
#                 .order_by(MarchLog.estimated_time)
#             )
#             target_logs = (await session.execute(stmt_t)).scalars().all()
#             print(f"   Found {len(target_logs)} checkpoints for Gayjoy.")

#         print("\n--- COLLISION CHECK LOGIC ---")

#         collision_candidates = []

#         for log in logs:
#             # Check DISTANCE to Target's static location (or current location snapshot)
#             dist = math.sqrt((log.x - TARGET_X) ** 2 + (log.y - TARGET_Y) ** 2)

#             if dist < closest_dist:
#                 closest_dist = dist
#                 closest_log = log

#             # DETAILED CHECK:
#             # If Gayjoy is moving, we need to find where Gayjoy was at log.estimated_time
#             if target_logs:
#                 # Find Gayjoy log closest to this time
#                 best_t_log = None
#                 min_t_diff = float("inf")

#                 for t_log in target_logs:
#                     dt = abs(
#                         (t_log.estimated_time - log.estimated_time).total_seconds()
#                     )
#                     if dt < min_t_diff:
#                         min_t_diff = dt
#                         best_t_log = t_log

#                 if best_t_log:
#                     # Distance between Orton Log and Gayjoy Log at similar time
#                     dynamic_dist = math.sqrt(
#                         (log.x - best_t_log.x) ** 2 + (log.y - best_t_log.y) ** 2
#                     )

#                     if dynamic_dist <= 25.0:
#                         collision_candidates.append(
#                             {
#                                 "time": log.estimated_time,
#                                 "orton_pos": (log.x, log.y),
#                                 "gayjoy_pos": (best_t_log.x, best_t_log.y),
#                                 "dist": dynamic_dist,
#                                 "time_sync_diff": min_t_diff,
#                             }
#                         )

#         # 4. Report Findings
#         if collision_candidates:
#             print(f"\n🚨 FOUND {len(collision_candidates)} VALID INTERCEPTIONS IN DB!")
#             for c in collision_candidates[:3]:  # Show first 3
#                 print(f"   Time: {c['time']} UTC")
#                 print(f"   Orton: {c['orton_pos']} | Gayjoy: {c['gayjoy_pos']}")
#                 print(
#                     f"   Dist: {c['dist']:.2f}px | Time Sync Gap: {c['time_sync_diff']:.1f}s"
#                 )
#         else:
#             print("\n✅ No recorded collisions found in current logs.")
#             print(f"   Closest approach (Static Check): {closest_dist:.2f}px")
#             if closest_log:
#                 print(
#                     f"   at {closest_log.estimated_time} UTC @ ({closest_log.x:.1f}, {closest_log.y:.1f})"
#                 )


# if __name__ == "__main__":
#     asyncio.run(analyze_march_logs())


import asyncio
import os
import math
from datetime import datetime
from sqlalchemy import select
from app.db.db_manager import get_session
from app.db.models import MarchLog

# CONFIGURATION
ID_A = 5006  # Gayjoy
ID_B = 6639  # Orton


async def debug_timeline():
    print("⏳ STARTING TIMELINE DIAGNOSTIC ⏳")

    async with get_session() as session:
        # Fetch Logs
        logs_a = (
            (
                await session.execute(
                    select(MarchLog)
                    .where(MarchLog.army_id == ID_A)
                    .order_by(MarchLog.estimated_time)
                )
            )
            .scalars()
            .all()
        )
        logs_b = (
            (
                await session.execute(
                    select(MarchLog)
                    .where(MarchLog.army_id == ID_B)
                    .order_by(MarchLog.estimated_time)
                )
            )
            .scalars()
            .all()
        )

        if not logs_a or not logs_b:
            print("❌ Logs missing.")
            return

        # Helper to print range
        def print_range(name, logs):
            start = logs[0].estimated_time
            end = logs[-1].estimated_time
            print(f"\n🔹 {name}:")
            print(f"   Start: {start} UTC")
            print(f"   End:   {end} UTC")
            print(f"   Total Duration: {(end - start).total_seconds()}s")
            print(f"   Log Count: {len(logs)}")
            return start, end

        start_a, end_a = print_range("Gayjoy", logs_a)
        start_b, end_b = print_range("Orton", logs_b)

        # CHECK OVERLAP
        latest_start = max(start_a, start_b)
        earliest_end = min(end_a, end_b)

        print("\n📊 OVERLAP ANALYSIS:")
        if latest_start < earliest_end:
            overlap = (earliest_end - latest_start).total_seconds()
            print(f"   ✅ There is a valid Time Overlap of {int(overlap)} seconds.")
            print(f"   From: {latest_start}")
            print(f"   To:   {earliest_end}")

            # --- DUAL INTERPOLATION CHECK ---
            print("\n🔬 Checking distance during overlap...")
            min_dist = float("inf")
            min_time = None

            # Check every 5 seconds inside the overlap
            for t in range(0, int(overlap), 5):
                current_time_abs = latest_start.timestamp() + t

                # Helper to get pos at specific timestamp
                def get_pos_at(logs, timestamp):
                    for i in range(len(logs) - 1):
                        t1 = logs[i].estimated_time.timestamp()
                        t2 = logs[i + 1].estimated_time.timestamp()
                        if t1 <= timestamp <= t2:
                            ratio = (timestamp - t1) / (t2 - t1) if (t2 - t1) > 0 else 0
                            x = logs[i].x + (logs[i + 1].x - logs[i].x) * ratio
                            y = logs[i].y + (logs[i + 1].y - logs[i].y) * ratio
                            return x, y
                    return None

                pos_a = get_pos_at(logs_a, current_time_abs)
                pos_b = get_pos_at(logs_b, current_time_abs)

                if pos_a and pos_b:
                    d = math.sqrt(
                        (pos_a[0] - pos_b[0]) ** 2 + (pos_a[1] - pos_b[1]) ** 2
                    )
                    if d < min_dist:
                        min_dist = d
                        min_time = datetime.fromtimestamp(current_time_abs)

            print(f"   🏁 Closest approach during overlap: {min_dist:.2f}px")
            if min_time:
                print(f"   At time: {min_time} UTC")

            if min_dist <= 25.0:
                print("   🚨 CONFIRMED: Valid Intersection found in DB Logs.")
            else:
                print(
                    "   ❌ MISSED: They overlapped in time, but were too far apart spatially."
                )

        else:
            print("   ❌ NO TIME OVERLAP.")
            print(
                "   Gayjoy and Orton were never moving at the same time in the current DB logs."
            )
            print("   This explains why interpolation failed.")


if __name__ == "__main__":
    asyncio.run(debug_timeline())
