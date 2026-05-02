import asyncio
import os
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.db_manager import get_session
from app.db.models import MarchLog, Army
from app.services.engine_manager import PF_ENGINE

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
ARMY_A_ID = 6639  # Lord Orton (The one that was shown as "intercepted")
ARMY_B_ID = 5006  # Gayjoy (The one that was shown as "intercepting")
# ==========================================


async def visualize_historic_intercept():
    print(f"🎬 STARTING HISTORIC INTERCEPT VISUALIZATION: {ARMY_A_ID} vs {ARMY_B_ID}")

    # 1. Initialize Map Engine (for visualization background)
    try:
        PF_ENGINE.initialize()
    except Exception as e:
        print(f"⚠️ Could not load map background: {e}. Using white background.")

    async with get_session() as session:
        # Fetch actual Army objects for names
        army_a_obj = await session.get(Army, ARMY_A_ID)
        army_b_obj = await session.get(Army, ARMY_B_ID)

        if not army_a_obj or not army_b_obj:
            print("❌ Error: One or both armies not found in DB.")
            return

        army_a_name = army_a_obj.commander_name if army_a_obj else f"Army {ARMY_A_ID}"
        army_b_name = army_b_obj.commander_name if army_b_obj else f"Army {ARMY_B_ID}"

        # 2. Fetch MarchLog entries for both armies
        logs_a = (
            (
                await session.execute(
                    select(MarchLog)
                    .where(MarchLog.army_id == ARMY_A_ID)
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
                    .where(MarchLog.army_id == ARMY_B_ID)
                    .order_by(MarchLog.estimated_time)
                )
            )
            .scalars()
            .all()
        )

        if not logs_a or not logs_b:
            print(
                "❌ No march logs found for one or both armies. Cannot reconstruct path."
            )
            print("   (This might happen if logs were cleared after the event).")
            return

        print(
            f"📜 Found {len(logs_a)} logs for {army_a_name} and {len(logs_b)} for {army_b_name}."
        )

        # 3. Determine overlapping time window
        start_time_a = logs_a[0].estimated_time
        end_time_a = logs_a[-1].estimated_time
        start_time_b = logs_b[0].estimated_time
        end_time_b = logs_b[-1].estimated_time

        overlap_start = max(start_time_a, start_time_b)
        overlap_end = min(end_time_a, end_time_b)

        if overlap_start >= overlap_end:
            print(
                "\n❌ No overlapping time window found in logs. They never moved simultaneously."
            )
            print(f"   {army_a_name} active: {start_time_a} to {end_time_a}")
            print(f"   {army_b_name} active: {start_time_b} to {end_time_b}")
            # Still plot the paths, but won't find a temporal intercept
            simulate_overlap = False
        else:
            print(
                f"\n✅ Overlapping movement detected from {overlap_start.strftime('%H:%M:%S')} to {overlap_end.strftime('%H:%M:%S')} UTC."
            )
            simulate_overlap = True

        # 4. Helper: Get interpolated position at a specific absolute time
        def get_interpolated_pos(logs, target_timestamp):
            if not logs:
                return None

            # Handle before first log or after last log
            if target_timestamp <= logs[0].estimated_time:
                return (logs[0].x, logs[0].y)
            if target_timestamp >= logs[-1].estimated_time:
                return (logs[-1].x, logs[-1].y)

            for i in range(len(logs) - 1):
                log1 = logs[i]
                log2 = logs[i + 1]

                if log1.estimated_time <= target_timestamp <= log2.estimated_time:
                    total_duration = (
                        log2.estimated_time - log1.estimated_time
                    ).total_seconds()
                    elapsed = (target_timestamp - log1.estimated_time).total_seconds()

                    if (
                        total_duration <= 0
                    ):  # Avoid division by zero if logs are at same time
                        return (log1.x, log1.y)

                    ratio = elapsed / total_duration
                    interp_x = log1.x + (log2.x - log1.x) * ratio
                    interp_y = log1.y + (log2.y - log1.y) * ratio
                    return (interp_x, interp_y)
            return None  # Should not happen if target_timestamp is within bounds

        # 5. Visualization Setup
        fig, ax = plt.subplots(figsize=(14, 14))

        if (
            hasattr(PF_ENGINE, "map_visual_rgb")
            and PF_ENGINE.map_visual_rgb is not None
        ):
            ax.imshow(PF_ENGINE.map_visual_rgb)
        else:
            ax.invert_yaxis()

        # Plot full logged paths
        ax.plot(
            [l.x for l in logs_a],
            [l.y for l in logs_a],
            color="cyan",
            linestyle="-",
            linewidth=2,
            alpha=0.7,
            label=f"Path: {army_a_name}",
        )
        ax.plot(
            [l.x for l in logs_b],
            [l.y for l in logs_b],
            color="orange",
            linestyle="-",
            linewidth=2,
            alpha=0.7,
            label=f"Path: {army_b_name}",
        )

        # Mark start/end points
        ax.scatter(
            logs_a[0].x,
            logs_a[0].y,
            color="darkcyan",
            marker=">",
            s=100,
            label=f"Start: {army_a_name}",
        )
        ax.scatter(
            logs_a[-1].x,
            logs_a[-1].y,
            color="darkcyan",
            marker="<",
            s=100,
            label=f"End: {army_a_name}",
        )
        ax.scatter(
            logs_b[0].x,
            logs_b[0].y,
            color="darkorange",
            marker=">",
            s=100,
            label=f"Start: {army_b_name}",
        )
        ax.scatter(
            logs_b[-1].x,
            logs_b[-1].y,
            color="darkorange",
            marker="<",
            s=100,
            label=f"End: {army_b_name}",
        )

        # Add labels to start/end points
        ax.annotate(
            f"{army_a_name} Start\n{start_time_a.strftime('%H:%M:%S')}",
            (logs_a[0].x, logs_a[0].y),
            textcoords="offset points",
            xytext=(5, 5),
            ha="left",
            fontsize=8,
            color="darkcyan",
        )
        ax.annotate(
            f"{army_b_name} Start\n{start_time_b.strftime('%H:%M:%S')}",
            (logs_b[0].x, logs_b[0].y),
            textcoords="offset points",
            xytext=(5, 5),
            ha="left",
            fontsize=8,
            color="darkorange",
        )

        # 6. Simulate within overlap to find closest approach
        closest_dist = float("inf")
        closest_moment_utc = None
        pos_a_at_closest = None
        pos_b_at_closest = None

        if simulate_overlap:
            simulation_step_seconds = (
                1  # More granular step for precise collision finding
            )

            # Use `range` on timestamps directly (converted to int seconds)
            overlap_start_sec = int(overlap_start.timestamp())
            overlap_end_sec = int(overlap_end.timestamp())

            for current_sec in range(
                overlap_start_sec,
                overlap_end_sec + simulation_step_seconds,
                simulation_step_seconds,
            ):
                current_time = datetime.fromtimestamp(
                    current_sec, tz=overlap_start.tzinfo
                )

                pos_a = get_interpolated_pos(logs_a, current_time)
                pos_b = get_interpolated_pos(logs_b, current_time)

                if pos_a and pos_b:
                    dist = math.sqrt(
                        (pos_a[0] - pos_b[0]) ** 2 + (pos_a[1] - pos_b[1]) ** 2
                    )

                    if dist < closest_dist:
                        closest_dist = dist
                        closest_moment_utc = current_time
                        pos_a_at_closest = pos_a
                        pos_b_at_closest = pos_b

            if closest_moment_utc and closest_dist <= 25.0:
                print(f"\n🚨 HISTORIC INTERCEPTION CONFIRMED!")
                print(f"   Time: {closest_moment_utc.strftime('%H:%M:%S.%f')[:-3]} UTC")
                print(
                    f"   {army_a_name} Pos:  ({pos_a_at_closest[0]:.0f}, {pos_a_at_closest[1]:.0f})"
                )
                print(
                    f"   {army_b_name} Pos: ({pos_b_at_closest[0]:.0f}, {pos_b_at_closest[1]:.0f})"
                )
                print(f"   Distance: {closest_dist:.2f} px (Threshold: 25.0 px)")

                # Draw collision marker and connecting line
                ax.plot(
                    pos_a_at_closest[0],
                    pos_a_at_closest[1],
                    "rX",
                    markersize=25,
                    markeredgecolor="white",
                    linewidth=2,
                    zorder=10,
                    label="Collision Point A",
                )
                ax.plot(
                    pos_b_at_closest[0],
                    pos_b_at_closest[1],
                    "rX",
                    markersize=25,
                    markeredgecolor="white",
                    linewidth=2,
                    zorder=10,
                    label="Collision Point B",
                )
                ax.plot(
                    [pos_a_at_closest[0], pos_b_at_closest[0]],
                    [pos_a_at_closest[1], pos_b_at_closest[1]],
                    "r--",
                    linewidth=2,
                    alpha=0.8,
                    zorder=9,
                )

                mid_x = (pos_a_at_closest[0] + pos_b_at_closest[0]) / 2
                mid_y = (pos_a_at_closest[1] + pos_b_at_closest[1]) / 2
                ax.annotate(
                    f"INTERCEPT!\nDist: {closest_dist:.1f}px",
                    xy=(mid_x, mid_y),
                    xytext=(mid_x + 30, mid_y - 30),
                    arrowprops=dict(facecolor="red", shrink=0.05),
                    color="red",
                    fontweight="bold",
                    fontsize=12,
                    bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="red", lw=2),
                )

            elif closest_moment_utc:
                print(f"\n✅ No historic interception (too far apart during overlap).")
                print(
                    f"   Closest approach: {closest_dist:.2f} px at {closest_moment_utc.strftime('%H:%M:%S.%f')[:-3]} UTC"
                )
                # Optionally draw closest approach marker
                ax.plot(
                    pos_a_at_closest[0],
                    pos_a_at_closest[1],
                    "yx",
                    markersize=15,
                    markeredgecolor="black",
                    zorder=10,
                    label="Closest A",
                )
                ax.plot(
                    pos_b_at_closest[0],
                    pos_b_at_closest[1],
                    "yx",
                    markersize=15,
                    markeredgecolor="black",
                    zorder=10,
                    label="Closest B",
                )
                ax.plot(
                    [pos_a_at_closest[0], pos_b_at_closest[0]],
                    [pos_a_at_closest[1], pos_b_at_closest[1]],
                    "y--",
                    linewidth=1,
                    alpha=0.6,
                    zorder=9,
                )

        # 7. Finalize and Save Image
        # Adjust limits to fit all logged points, plus some margin
        all_x = [l.x for l in logs_a + logs_b]
        all_y = [l.y for l in logs_a + logs_b]
        if all_x and all_y:
            margin_x = (max(all_x) - min(all_x)) * 0.1 or 100  # At least 100px margin
            margin_y = (max(all_y) - min(all_y)) * 0.1 or 100
            ax.set_xlim(min(all_x) - margin_x, max(all_x) + margin_x)
            ax.set_ylim(
                max(all_y) + margin_y, min(all_y) - margin_y
            )  # Y-axis inverted for map coords

        ax.legend(loc="upper right", fontsize=10, fancybox=True, framealpha=0.8)
        ax.set_title(f"Historic Intercept: {army_a_name} vs {army_b_name}", fontsize=16)
        ax.set_xlabel("X Coordinate")
        ax.set_ylabel("Y Coordinate")
        ax.grid(True, linestyle=":", alpha=0.6)

        filename = ROOT / "debug" / "historic_interception.png"
        plt.savefig(filename, bbox_inches="tight", dpi=150)
        print(f"\n🖼️  Generated map: {filename}")


if __name__ == "__main__":
    asyncio.run(visualize_historic_intercept())
