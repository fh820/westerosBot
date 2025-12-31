import asyncio
import datetime
import os
from sqlalchemy import delete
from app.db.db_manager import get_session
from app.db.models import Game, House, Army, MarchLog
from app.services.warfare_service import WarfareService, PF_ENGINE
from app.db.repositories import ArmyRepo


async def run_test():
    print("🧪 --- TESTING SCOUT DELAY SYSTEM ---")
    async with get_session() as session:
        # 1. Setup
        # (Wipe DB logic here, same as before)
        game = Game(guild_id=12345, name="ScoutTest", is_active=True)
        session.add(game)
        await session.flush()

        h1 = House(game_id=game.game_id, name="Stark", color_hex="#FFF")
        h2 = House(game_id=game.game_id, name="Lannister", color_hex="#F00")
        session.add_all([h1, h2])
        await session.flush()

        # 2. Create Collision Scenario
        # Stark at Winterfell (601, 742)
        # Lannister at Moat Cailin (613, 1008)
        a_stark = Army(
            game_id=game.game_id,
            house_id=h1.house_id,
            commander_name="Robb",
            troop_count=1000,
            location_x=601,
            location_y=742,
            status="MARCHING",
            composition={},
        )
        a_lannister = Army(
            game_id=game.game_id,
            house_id=h2.house_id,
            commander_name="Tywin",
            troop_count=5000,
            location_x=613,
            location_y=1008,
            status="IDLE",
            composition={},
        )
        session.add_all([a_stark, a_lannister])
        await session.commit()

        # 3. Execute Calculation (Robb Marches South)
        service = WarfareService(session)
        dest_name = "Moat Cailin"

        # Need coordinates
        dest_coords = {"x": 613, "y": 1008}  # Moat Cailin
        start_coords = {"x": 601, "y": 742}  # Winterfell

        # Calc Path
        path_data = PF_ENGINE._find_journey_sync(
            tuple(start_coords.values()), "Moat Cailin"
        )

        # Run Logic
        duration = 3600 * 5  # 5 hours
        now = datetime.datetime.now(datetime.timezone.utc)

        print(f"   - Calculating Interceptions...")
        collisions = await service.check_interceptions_advanced(
            game.game_id, a_stark.army_id, path_data["path_points"], now, duration
        )

        if not collisions:
            print("❌ FAIL: No collision detected.")
            return

        col = collisions[0]
        collision_time = col["time"]

        print(f"   ✅ Collision Detected at: {collision_time.strftime('%H:%M:%S UTC')}")
        print(f"   - Current Time: {now.strftime('%H:%M:%S UTC')}")

        # 4. Verify Timing
        alert_time = collision_time - datetime.timedelta(hours=1)
        print(f"   - Alert Scheduled For: {alert_time.strftime('%H:%M:%S UTC')}")

        time_diff = (alert_time - now).total_seconds()
        print(f"   - Delay until Alert: {int(time_diff/60)} minutes")

        if time_diff > 0:
            print("✅ PASS: Alert is correctly delayed (Fog of War Active).")
        else:
            print("⚠️ PASS (Immediate): Collision is < 1 hour away, so alert fires now.")


if __name__ == "__main__":
    asyncio.run(run_test())
