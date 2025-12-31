import json
import os
from sqlalchemy import select, update, text
from app.db.models import Game, House, Fief


class ScenarioService:
    def __init__(self, session):
        self.session = session

    async def load_scenario(self, guild_id: int, scenario_filename: str):
        """
        Applies a historical patch to the existing world.
        """
        # 1. Locate File
        # Assuming scenarios are in 'app/scenarios/'
        path = os.path.join("app", "scenarios", f"{scenario_filename}.json")

        if not os.path.exists(path):
            return False, f"❌ Scenario file not found: `{path}`"

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return False, f"❌ JSON Error: {e}"

        # 2. Get Game
        stmt = select(Game).where(Game.guild_id == guild_id, Game.is_active == True)
        result = await self.session.execute(stmt)
        game = result.scalars().first()

        if not game:
            return False, "❌ No active game found. Run `!setup_game` first."

        updates_log = []

        # 3. Update Game Meta
        game.current_year = data["meta"].get("year", 298)

        # 4. Process Updates
        print(f"--- 📜 Applying Scenario: {data['meta']['name']} ---")

        for change in data["updates"]:
            castle_name = change.get("castle")

            # Find the Fief (Land)
            stmt = select(Fief).where(
                Fief.game_id == game.game_id, Fief.name == castle_name
            )
            result = await self.session.execute(stmt)
            fief = result.scalars().first()

            if not fief:
                print(f"⚠️ Skipped: Castle '{castle_name}' not found.")
                continue

            # A. Update Fief Stats (Income / Ruin Status)
            if "base_income" in change:
                fief.base_income = change["base_income"]

            if "is_ruined" in change:
                fief.is_ruined = change["is_ruined"]
                # If un-ruining, ensure type is feudal, not ruin
                if not fief.is_ruined:
                    fief.fief_type = "feudal"

            # B. Handle Owner Changes (Renaming)
            # This is powerful: We rename "Whent" to "Strong" directly.
            if "rename_owner_to" in change:
                stmt_owner = select(House).where(House.house_id == fief.owner_id)
                res_owner = await self.session.execute(stmt_owner)
                owner = res_owner.scalars().first()

                if owner:
                    old_name = owner.name
                    new_name = change["rename_owner_to"]
                    if old_name != new_name:
                        owner.name = new_name
                        updates_log.append(
                            f"🔄 {castle_name}: House {old_name} became **House {new_name}**"
                        )
            if "create_house" in change:
                new_house_data = change["create_house"]
                # Check if exists
                stmt_check = select(House).where(
                    House.game_id == game.game_id, House.name == new_house_data["name"]
                )
                res_check = await self.session.execute(stmt_check)
                if not res_check.scalars().first():
                    new_house = House(
                        game_id=game.game_id,
                        name=new_house_data["name"],
                        color_hex=new_house_data.get("color", "#000000"),
                        treasury=new_house_data.get("treasury", 0),
                        house_type="faction",
                    )
                    self.session.add(new_house)
                    await self.session.flush()  # Get ID
                    updates_log.append(f"🆕 Faction Created: **{new_house.name}**")

                    # If this new house should own the castle immediately
                    if change.get("castle"):
                        # Re-fetch fief to be safe
                        stmt_f = select(Fief).where(
                            Fief.game_id == game.game_id,
                            Fief.name == change.get("castle"),
                        )
                        f = (await self.session.execute(stmt_f)).scalars().first()
                        if f:
                            f.owner_id = new_house.house_id
                            updates_log.append(
                                f"🏰 **{f.name}** is now the seat of **{new_house.name}**"
                            )

        # 5. Recalculate Treasuries based on new incomes
        print("🔄 Recalculating Treasuries for the new era...")

        await self.session.execute(
            text(
                """
            UPDATE houses 
            SET treasury = (
                SELECT COALESCE(SUM(base_income), 0) * 2 
                FROM fiefs 
                WHERE fiefs.owner_id = houses.house_id
            )
            WHERE game_id = :game_id
            """
            ),
            {"game_id": game.game_id},
        )

        await self.session.commit()

        return True, (
            f"📜 **Scenario Loaded: {data['meta']['name']} ({game.current_year} AC)**\n"
            f"✅ Applied {len(data['updates'])} historical patches.\n"
            f"_{data['meta']['description']}_"
        )
