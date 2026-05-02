import asyncio
import os
import math
from datetime import datetime
import sys
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.db_manager import get_session
from app.db.models import MarchLog
from app.services.engine_manager import PF_ENGINE

# ==========================================
# CONFIGURATION
# ==========================================
GAYJOY_ID = 5006
ORTON_ID = 6639
# ==========================================


async def visualize_logs():
    print("🎨 STARTING DB VISUALIZATION 🎨")

    # Initialize map engine for background image (Optional)
    try:
        PF_ENGINE.initialize()
    except:
        print("⚠️ Could not load map background, using white background.")

    async with get_session() as session:
        # 1. Fetch Logs
        logs_g = (
            (
                await session.execute(
                    select(MarchLog)
                    .where(MarchLog.army_id == GAYJOY_ID)
                    .order_by(MarchLog.estimated_time)
                )
            )
            .scalars()
            .all()
        )
        logs_o = (
            (
                await session.execute(
                    select(MarchLog)
                    .where(MarchLog.army_id == ORTON_ID)
                    .order_by(MarchLog.estimated_time)
                )
            )
            .scalars()
            .all()
        )

        if not logs_g or not logs_o:
            print("❌ Logs missing.")
            return

        # 2. Setup Plot
        fig, ax = plt.subplots(figsize=(14, 14))

        # Load Background if available
        if (
            hasattr(PF_ENGINE, "map_visual_rgb")
            and PF_ENGINE.map_visual_rgb is not None
        ):
            ax.imshow(PF_ENGINE.map_visual_rgb)
        else:
            ax.invert_yaxis()  # Standard map coords (0,0 is top left)

        # 3. Helper: Plot Path
        def plot_path(logs, color, label, marker):
            x = [l.x for l in logs]
            y = [l.y for l in logs]
            times = [l.estimated_time for l in logs]

            # Draw Line
            ax.plot(
                x,
                y,
                color=color,
                linestyle="-",
                linewidth=2,
                alpha=0.6,
                label=f"{label} Path",
            )

            # Draw Checkpoints
            ax.scatter(x, y, color=color, marker=marker, s=60, zorder=5)

            # Label Start/End times
            ax.text(
                x[0],
                y[0],
                f"START\n{times[0].strftime('%H:%M:%S')}",
                fontsize=9,
                color=color,
                fontweight="bold",
            )
            ax.text(
                x[-1],
                y[-1],
                f"END\n{times[-1].strftime('%H:%M:%S')}",
                fontsize=9,
                color=color,
            )

        # Plot Gayjoy (Orange)
        plot_path(logs_g, "orange", "Gayjoy", "s")

        # Plot Orton (Cyan)
        plot_path(logs_o, "cyan", "Orton", "o")

        # 4. THE SMOKING GUN: Time Comparison
        # Orton's Log starts at: 15:36:57
        orton_start_time = logs_o[0].estimated_time
        orton_start_pos = (logs_o[0].x, logs_o[0].y)

        # Find where Gayjoy was at 15:36:57
        gayjoy_pos_at_intercept = None

        # Linear Interpolation logic
        target_ts = orton_start_time.timestamp()

        for i in range(len(logs_g) - 1):
            t1 = logs_g[i].estimated_time.timestamp()
            t2 = logs_g[i + 1].estimated_time.timestamp()

            if t1 <= target_ts <= t2:
                ratio = (target_ts - t1) / (t2 - t1)
                gx = logs_g[i].x + (logs_g[i + 1].x - logs_g[i].x) * ratio
                gy = logs_g[i].y + (logs_g[i + 1].y - logs_g[i].y) * ratio
                gayjoy_pos_at_intercept = (gx, gy)
                break

        # 5. Annotate the Intercept
        if gayjoy_pos_at_intercept:
            dist = math.sqrt(
                (orton_start_pos[0] - gayjoy_pos_at_intercept[0]) ** 2
                + (orton_start_pos[1] - gayjoy_pos_at_intercept[1]) ** 2
            )

            print(f"\n📊 MOMENT OF TRUTH ({orton_start_time.strftime('%H:%M:%S')}):")
            print(f"   Orton Logged Pos:  {orton_start_pos}")
            print(f"   Gayjoy Calc Pos:   {gayjoy_pos_at_intercept}")
            print(f"   Distance:          {dist:.2f} px")

            # Draw a bright red line connecting them
            ax.plot(
                [orton_start_pos[0], gayjoy_pos_at_intercept[0]],
                [orton_start_pos[1], gayjoy_pos_at_intercept[1]],
                color="red",
                linewidth=3,
                linestyle="--",
                zorder=10,
            )

            ax.scatter(
                gayjoy_pos_at_intercept[0],
                gayjoy_pos_at_intercept[1],
                color="red",
                marker="X",
                s=150,
                zorder=10,
                label=f"Gayjoy @ {orton_start_time.strftime('%H:%M:%S')}",
            )

            mid_x = (orton_start_pos[0] + gayjoy_pos_at_intercept[0]) / 2
            mid_y = (orton_start_pos[1] + gayjoy_pos_at_intercept[1]) / 2

            ax.annotate(
                f"INTERCEPT\nDist: {dist:.1f}px",
                xy=(mid_x, mid_y),
                xytext=(mid_x + 20, mid_y - 20),
                arrowprops=dict(facecolor="red", shrink=0.05),
                color="red",
                fontweight="bold",
                fontsize=12,
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="red", lw=2),
            )

        # 6. Zoom to relevant area (optional, remove if you want full map)
        # Calculate bounds
        all_x = [l.x for l in logs_g] + [l.x for l in logs_o]
        all_y = [l.y for l in logs_g] + [l.y for l in logs_o]
        margin = 100
        ax.set_xlim(min(all_x) - margin, max(all_x) + margin)
        ax.set_ylim(max(all_y) + margin, min(all_y) - margin)  # Y is inverted on maps

        ax.legend()
        ax.set_title(
            f"DB Log Reconstruction: The Moment of Intercept ({orton_start_time.strftime('%H:%M:%S')} UTC)",
            fontsize=16,
        )

        output_path = ROOT / "debug" / "db_log_visualization.png"
        plt.savefig(output_path)
        print("\n🖼️  Generated 'db_log_visualization.png'")


if __name__ == "__main__":
    asyncio.run(visualize_logs())
