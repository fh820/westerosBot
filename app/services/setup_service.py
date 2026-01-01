import json
import os
from sqlalchemy.future import select
from app.db.models import Game, House, User, GamePlayer, Army, Fief
from app.db.db_manager import get_session
from sqlalchemy import update, func


class SetupService:
    def __init__(self, session):
        self.session = session

    # async def init_world(
    #     self, guild_id: int, json_path: str, ruling_house_name: str = "Baratheon"
    # ):
    #     print(f"--- 🌍 Starting World Initialization for Guild {guild_id} ---")

    #     if not os.path.exists(json_path):
    #         return False, f"❌ Error: `{json_path}` not found."

    #     try:
    #         with open(json_path, "r", encoding="utf-8") as f:
    #             world_data = json.load(f)
    #     except Exception as e:
    #         return False, f"❌ JSON Error: {e}"

    #     # 1. Check for Game
    #     stmt = select(Game).where(Game.guild_id == guild_id, Game.is_active == True)
    #     result = await self.session.execute(stmt)
    #     existing_game = result.scalars().first()
    #     if existing_game:
    #         return False, f"⚠️ Game Active. Run `!end_game CONFIRM PURGE` first."

    #     # 2. Create Game
    #     print(f"Creating new Game... Crown: {ruling_house_name}")
    #     game = Game(
    #         guild_id=guild_id, name="Westeros Campaign", ruling_house=ruling_house_name
    #     )
    #     self.session.add(game)
    #     await self.session.flush()

    #     house_name_to_id = {}
    #     fiefs_created = 0

    #     # ====================================================
    #     # 🔄 PASS 1: CREATE HOUSES & ARMIES
    #     # ====================================================
    #     print(f"🔄 PASS 1: Building locations...")

    #     for entry in world_data:
    #         raw_house_name = entry.get("house", "Unknown")
    #         fief_name = entry.get("castle", "Unknown")

    #         # Resolve [CROWN] owner
    #         house_name = (
    #             ruling_house_name
    #             if raw_house_name in ["[CROWN]", "[CROWN_HEIR]"]
    #             else raw_house_name
    #         )

    #         # Create House if missing
    #         if house_name in house_name_to_id:
    #             owner_id = house_name_to_id[house_name]
    #         else:
    #             house_obj = House(
    #                 game_id=game.game_id,
    #                 name=house_name,
    #                 treasury=100,
    #                 color_hex="#FFFFFF",
    #                 is_ruined=entry.get("is_ruined", False),
    #             )
    #             self.session.add(house_obj)
    #             await self.session.flush()
    #             owner_id = house_obj.house_id
    #             house_name_to_id[house_name] = owner_id

    #         # Ratio: 1 Gold = 1.5 Manpower
    #         base_income = entry.get("base_income", 0)
    #         base_manpower = int(base_income * 1.5)

    #         # Create Fief
    #         fief = Fief(
    #             game_id=game.game_id,
    #             owner_id=owner_id,
    #             name=fief_name,
    #             region=entry.get("region", "Unknown"),
    #             location_x=entry.get("x", 0),
    #             location_y=entry.get("y", 0),
    #             base_income=entry.get("base_income", 0),
    #             fief_type=entry.get("house_type", "feudal"),
    #             is_ruined=entry.get("is_ruined", False),
    #             base_manpower=base_manpower,
    #         )
    #         self.session.add(fief)
    #         fiefs_created += 1

    #         # --- 🛡️ ARMY CREATION (THE FIX) 🛡️ ---
    #         all_stats = entry.get("army_stats", {})
    #         if not all_stats:
    #             continue

    #         # 1. Land Army
    #         land_comp = {
    #             "infantry": all_stats.get("infantry", 0),
    #             "cavalry": all_stats.get("cavalry", 0),
    #             "archers": all_stats.get("archers", 0),
    #         }
    #         land_total = sum(land_comp.values())

    #         if land_total > 0:
    #             land_army = Army(
    #                 game_id=game.game_id,
    #                 house_id=owner_id,
    #                 army_type="LAND",
    #                 commander_name=f"Garrison of {fief_name}",
    #                 troop_count=land_total,
    #                 composition=land_comp,
    #                 location_x=entry.get("x", 0),
    #                 location_y=entry.get("y", 0),
    #                 status="GARRISONED",
    #             )
    #             self.session.add(land_army)

    #         # 2. Sea Army (Fleet)
    #         ship_count = all_stats.get("ships", 0)
    #         if ship_count > 0:
    #             fleet = Army(
    #                 game_id=game.game_id,
    #                 house_id=owner_id,
    #                 army_type="SEA",
    #                 commander_name=f"Fleet of {fief_name}",
    #                 troop_count=ship_count,  # Troop count for fleets is just the number of ships
    #                 composition={"ships": ship_count},
    #                 location_x=entry.get("x", 0),
    #                 location_y=entry.get("y", 0),
    #                 status="GARRISONED",  # Fleets are "Garrisoned" at port
    #             )
    #             self.session.add(fleet)

    #     # ====================================================
    #     # 🔄 PASS 2: LINK LIEGE LORDS
    #     # ====================================================
    #     print("🔄 PASS 2: Linking Feudal Hierarchy...")

    #     # Ensure the ruling house exists
    #     crown_id = house_name_to_id.get(ruling_house_name)

    #     linked_count = 0
    #     for entry in world_data:
    #         # We must use the RESOLVED name here too
    #         raw_house = entry.get("house")
    #         if raw_house in ["[CROWN]", "[CROWN_HEIR]"]:
    #             house_name = ruling_house_name
    #         else:
    #             house_name = raw_house

    #         liege_name = entry.get("liege")

    #         if not house_name or not liege_name:
    #             continue

    #         vassal_id = house_name_to_id.get(house_name)
    #         target_liege_id = None

    #         # Resolve Liege Tag
    #         if liege_name in ["[CROWN]", "[CROWN_HEIR]"]:
    #             target_liege_id = crown_id
    #         elif liege_name in house_name_to_id:
    #             target_liege_id = house_name_to_id[liege_name]

    #         if vassal_id and target_liege_id and vassal_id != target_liege_id:
    #             house_obj = await self.session.get(House, vassal_id)
    #             if house_obj:
    #                 house_obj.liege_id = target_liege_id
    #                 linked_count += 1

    #     # ====================================================
    #     # 🔄 PASS 3: ECONOMY
    #     # ====================================================
    #     await self.calculate_initial_treasury(game.game_id)
    #     # await self.session.commit()

    #     print("🔄 PASS 4: Calculating Starting Manpower...")
    #     # 👇👇👇 THIS IS THE LINE THAT WAS MISSING/COMMENTED OUT 👇👇👇
    #     await self.calculate_initial_manpower(game.game_id)

    #     await self.session.commit()

    #     return True, (
    #         f"✅ Setup Complete for **House {ruling_house_name}** reign!\n"
    #         f"🏰 Houses: {len(house_name_to_id)} | Fiefs: {fiefs_created}\n"
    #         f"🔗 Vassals Linked: {linked_count}"
    #     )
    async def init_world(
        self,
        guild_id: int,
        gm_discord_id: int,
        json_path: str,
        ruling_house_name: str = "Baratheon",
    ):
        print(f"--- 🌍 Starting World Initialization for Guild {guild_id} ---")

        if not os.path.exists(json_path):
            return False, f"❌ Error: `{json_path}` not found."

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                world_data = json.load(f)
        except Exception as e:
            return False, f"❌ JSON Error: {e}"

        # 1. Check for Game
        stmt = select(Game).where(Game.guild_id == guild_id, Game.is_active == True)
        result = await self.session.execute(stmt)
        existing_game = result.scalars().first()
        if existing_game:
            return False, f"⚠️ Game Active. Run `!end_game CONFIRM PURGE` first."

        # 2. Create Game
        print(f"Creating new Game... Crown: {ruling_house_name}")
        game = Game(
            guild_id=guild_id, name="Westeros Campaign", ruling_house=ruling_house_name
        )
        self.session.add(game)
        await self.session.flush()

        # --- NEW: GET OR CREATE GM USER AND SET is_gm FLAG ---
        gm_user_stmt = select(User).where(User.discord_id == gm_discord_id)
        gm_user = (await self.session.execute(gm_user_stmt)).scalars().first()
        if not gm_user:
            print(
                f"[Setup] Creating new User record for GM with Discord ID: {gm_discord_id}"
            )
            gm_user = User(discord_id=gm_discord_id, is_gm=True)
            self.session.add(gm_user)
        else:
            print(f"[Setup] Found existing User record for GM. Setting is_gm=True.")
            gm_user.is_gm = True
        await self.session.flush()
        # --- END NEW ---

        house_name_to_id = {}
        fiefs_created = 0

        # ====================================================
        # 🔄 PASS 1: CREATE HOUSES & ARMIES
        # ====================================================
        print(f"🔄 PASS 1: Building locations...")

        for entry in world_data:
            raw_house_name = entry.get("house", "Unknown")
            fief_name = entry.get("castle", "Unknown")

            # Resolve [CROWN] owner
            house_name = (
                ruling_house_name
                if raw_house_name in ["[CROWN]", "[CROWN_HEIR]"]
                else raw_house_name
            )

            # Create House if missing
            if house_name in house_name_to_id:
                owner_id = house_name_to_id[house_name]
            else:
                house_obj = House(
                    game_id=game.game_id,
                    name=house_name,
                    treasury=100,
                    color_hex="#FFFFFF",
                    is_ruined=entry.get("is_ruined", False),
                )
                self.session.add(house_obj)
                await self.session.flush()
                owner_id = house_obj.house_id
                house_name_to_id[house_name] = owner_id

            # Ratio: 1 Gold = 1.5 Manpower
            base_income = entry.get("base_income", 0)
            base_manpower = int(base_income * 1.5)

            # Create Fief
            fief = Fief(
                game_id=game.game_id,
                owner_id=owner_id,
                name=fief_name,
                region=entry.get("region", "Unknown"),
                location_x=entry.get("x", 0),
                location_y=entry.get("y", 0),
                base_income=entry.get("base_income", 0),
                fief_type=entry.get("house_type", "feudal"),
                is_ruined=entry.get("is_ruined", False),
                base_manpower=base_manpower,
            )
            self.session.add(fief)
            fiefs_created += 1

            # --- ARMY CREATION ---
            all_stats = entry.get("army_stats", {})
            if not all_stats:
                continue

            # 1. Land Army
            land_comp = {
                "infantry": all_stats.get("infantry", 0),
                "cavalry": all_stats.get("cavalry", 0),
                "archers": all_stats.get("archers", 0),
            }
            land_total = sum(land_comp.values())

            if land_total > 0:
                land_army = Army(
                    game_id=game.game_id,
                    house_id=owner_id,
                    army_type="LAND",
                    commander_name=f"Garrison of {fief_name}",
                    troop_count=land_total,
                    composition=land_comp,
                    location_x=entry.get("x", 0),
                    location_y=entry.get("y", 0),
                    status="GARRISONED",
                )
                self.session.add(land_army)

            # 2. Sea Army (Fleet)
            ship_count = all_stats.get("ships", 0)
            if ship_count > 0:
                fleet = Army(
                    game_id=game.game_id,
                    house_id=owner_id,
                    army_type="SEA",
                    commander_name=f"Fleet of {fief_name}",
                    troop_count=ship_count,
                    composition={"ships": ship_count},
                    location_x=entry.get("x", 0),
                    location_y=entry.get("y", 0),
                    status="GARRISONED",
                )
                self.session.add(fleet)

        # ====================================================
        # 🔄 PASS 2: LINK LIEGE LORDS
        # ====================================================
        print("🔄 PASS 2: Linking Feudal Hierarchy...")

        crown_id = house_name_to_id.get(ruling_house_name)

        linked_count = 0
        for entry in world_data:
            raw_house = entry.get("house")
            if raw_house in ["[CROWN]", "[CROWN_HEIR]"]:
                house_name = ruling_house_name
            else:
                house_name = raw_house

            liege_name = entry.get("liege")

            if not house_name or not liege_name:
                continue

            vassal_id = house_name_to_id.get(house_name)
            target_liege_id = None

            if liege_name in ["[CROWN]", "[CROWN_HEIR]"]:
                target_liege_id = crown_id
            elif liege_name in house_name_to_id:
                target_liege_id = house_name_to_id[liege_name]

            if vassal_id and target_liege_id and vassal_id != target_liege_id:
                house_obj = await self.session.get(House, vassal_id)
                if house_obj:
                    house_obj.liege_id = target_liege_id
                    linked_count += 1

        # ====================================================
        # 🔄 PASS 3: ECONOMY & MANPOWER
        # ====================================================
        await self.calculate_initial_treasury(game.game_id)
        print("🔄 PASS 4: Calculating Starting Manpower...")
        await self.calculate_initial_manpower(game.game_id)

        await self.session.commit()

        return True, (
            f"✅ Setup Complete for **House {ruling_house_name}** reign!\n"
            f"🏰 Houses: {len(house_name_to_id)} | Fiefs: {fiefs_created}\n"
            f"🔗 Vassals Linked: {linked_count}"
        )

    async def calculate_initial_treasury(self, game_id: int):
        """
        Sets every House's starting treasury to 2x the sum of their Fiefs' income.
        This version correctly ignores income from ruined fiefs.
        """
        # Subquery to sum base_income per owner from NON-RUINED fiefs
        income_subquery = (
            select(func.sum(Fief.base_income))
            .where(
                (Fief.owner_id == House.house_id)  # Link to the correct house
                & (
                    Fief.is_ruined == False
                )  # FIX: Only include non-ruined fiefs in the sum
            )
            .correlate(House)
            .scalar_subquery()
        )

        # Update treasury to 2x the calculated income
        stmt = (
            update(House)
            .where(House.game_id == game_id)
            .values(treasury=func.coalesce(income_subquery, 0) * 2)
        )
        await self.session.execute(stmt)

    async def calculate_initial_manpower(self, game_id: int):
        """
        Sets every House's manpower and manpower_cap to the sum of their Fiefs' manpower.
        """
        # Subquery to sum manpower per owner
        manpower_subquery = (
            select(func.sum(Fief.base_manpower))
            .where(Fief.owner_id == House.house_id)
            .correlate(House)
            .scalar_subquery()
        )

        # Update both current and max manpower
        stmt = (
            update(House)
            .where(House.game_id == game_id)
            .values(
                manpower=func.coalesce(manpower_subquery, 0),
                manpower_cap=func.coalesce(manpower_subquery, 0),
            )
        )
        await self.session.execute(stmt)
