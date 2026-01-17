import json
import os
from sqlalchemy.future import select
from app.db.models import Game, House, User, GamePlayer, Army, Fief
from app.db.db_manager import get_session
from sqlalchemy import update, func


class SetupService:
    def __init__(self, session):
        self.session = session

    async def init_world(
        self,
        guild_id: int,
        gm_discord_id: int,
        world_data: list,  # <--- CHANGED: Now accepts the data list directly
        ruling_house_name: str = "Baratheon",
        era_mode: str = "SPLIT",  # Options: "SPLIT" or "UNIFIED"
    ):
        print(
            f"--- 🌍 Starting World Initialization for Guild {guild_id} ({era_mode}) ---"
        )

        # 1. Validate Data (Replaces file check)
        if not isinstance(world_data, list) or not world_data:
            return (
                False,
                "❌ Error: Invalid World Data provided (Must be a non-empty list).",
            )

        # 2. Check for Active Game
        stmt = select(Game).where(Game.guild_id == guild_id, Game.is_active == True)
        result = await self.session.execute(stmt)
        if result.scalars().first():
            return False, f"⚠️ Game Active. Run `!end_game CONFIRM PURGE` first."

        # 3. Create Game
        game = Game(
            guild_id=guild_id,
            name=f"Westeros Campaign ({ruling_house_name})",
            ruling_house=ruling_house_name,
        )
        self.session.add(game)
        await self.session.flush()

        # 4. GM Setup
        gm_user_stmt = select(User).where(User.discord_id == gm_discord_id)
        gm_user = (await self.session.execute(gm_user_stmt)).scalars().first()
        if not gm_user:
            gm_user = User(discord_id=gm_discord_id, is_gm=True)
            self.session.add(gm_user)
        else:
            gm_user.is_gm = True
        await self.session.flush()

        house_name_to_id = {}

        # Track these dynamically to ensure Pass 2 links to the correct string
        crown_house_name = None
        heir_house_name = None
        ancestral_house_name = None  # For Storm's End/Summerhall specific logic

        fiefs_created = 0

        # ====================================================
        # 🔄 PASS 1: CREATE UNIQUE HOUSES, FIEFS & ARMIES
        # ====================================================
        print(f"🔄 PASS 1: Building locations...")

        for entry in world_data:
            raw_house_name = entry.get("house", "Unknown")
            fief_name = entry.get("castle", "Unknown")

            # --- DYNAMIC NAMING LOGIC ---
            if raw_house_name == "[CROWN]":
                if era_mode == "UNIFIED":
                    house_name = ruling_house_name
                else:
                    house_name = f"{ruling_house_name} of King's Landing"
                crown_house_name = house_name

            elif raw_house_name == "[CROWN_HEIR]":
                # Heir is almost always split (Dragonstone)
                house_name = f"{ruling_house_name} of Dragonstone"
                heir_house_name = house_name

            elif raw_house_name == ruling_house_name and fief_name in [
                "Storm's End",
                "Summerhall",
            ]:
                # Handling the Ancestral Seat of the King
                if era_mode == "UNIFIED":
                    house_name = ruling_house_name  # Merges with CROWN
                else:
                    house_name = f"{ruling_house_name} of {fief_name}"  # e.g. "Baratheon of Storm's End"
                ancestral_house_name = house_name

            else:
                house_name = raw_house_name
            # ---------------------------

            # Create House if missing (Deduplication happens here via dictionary key)
            if house_name in house_name_to_id:
                owner_id = house_name_to_id[house_name]
            else:
                house_obj = House(
                    game_id=game.game_id,
                    name=house_name,
                    treasury=0,
                    color_hex="#FFFFFF",
                    is_ruined=entry.get("is_ruined", False),
                    tax_rate=0.10,
                )
                self.session.add(house_obj)
                await self.session.flush()
                owner_id = house_obj.house_id
                house_name_to_id[house_name] = owner_id

            # Create Fief
            base_income = entry.get("base_income", 0)
            is_ruined = entry.get("is_ruined", False)

            fief = Fief(
                game_id=game.game_id,
                owner_id=owner_id,
                name=fief_name,
                region=entry.get("region", "Unknown"),
                location_x=entry.get("x", 0),
                location_y=entry.get("y", 0),
                base_income=base_income,
                fief_type=entry.get("house_type", "feudal"),
                is_ruined=is_ruined,
                base_manpower=int(base_income * 1.5),
                integration=(0.10 if is_ruined else 1.0),
            )
            self.session.add(fief)
            fiefs_created += 1

            # Army Creation
            all_stats = entry.get("army_stats", {})
            if all_stats and not is_ruined:
                land_comp = {
                    "infantry": all_stats.get("infantry", 0),
                    "cavalry": all_stats.get("cavalry", 0),
                    "archers": all_stats.get("archers", 0),
                }
                if sum(land_comp.values()) > 0:
                    self.session.add(
                        Army(
                            game_id=game.game_id,
                            house_id=owner_id,
                            army_type="LAND",
                            commander_name=f"Garrison of {fief_name}",
                            troop_count=sum(land_comp.values()),
                            composition=land_comp,
                            location_x=entry.get("x", 0),
                            location_y=entry.get("y", 0),
                            status="GARRISONED",
                        )
                    )

                ship_count = all_stats.get("ships", 0)
                if ship_count > 0:
                    self.session.add(
                        Army(
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
                    )

        # ====================================================
        # 🔄 PASS 2: LINK FEUDAL HIERARCHY
        # ====================================================
        print("🔄 PASS 2: Linking Feudal Hierarchy...")

        linked_count = 0
        for entry in world_data:
            # 1. Determine Vassal Name (Must match Pass 1 logic exactly)
            raw_h = entry.get("house")
            f_name = entry.get("castle")

            if raw_h == "[CROWN]":
                vassal_name = crown_house_name
            elif raw_h == "[CROWN_HEIR]":
                vassal_name = heir_house_name
            elif raw_h == ruling_house_name and f_name in ["Storm's End", "Summerhall"]:
                vassal_name = ancestral_house_name
            else:
                vassal_name = raw_h

            # 2. Determine Liege Name
            raw_liege = entry.get("liege")
            if not raw_liege:
                continue

            target_liege_name = raw_liege  # Default

            if raw_liege == "[CROWN]":
                target_liege_name = crown_house_name
            elif raw_liege == "[CROWN_HEIR]":
                target_liege_name = heir_house_name
            elif raw_liege == ruling_house_name:
                # If JSON says "Liege: Baratheon", we need to know WHICH Baratheon
                if entry.get("region") == "The Stormlands":
                    target_liege_name = ancestral_house_name  # Storm's End branch
                else:
                    target_liege_name = crown_house_name  # King's Landing branch

            # 3. Link them
            v_id = house_name_to_id.get(vassal_name)
            l_id = house_name_to_id.get(target_liege_name)

            if v_id and l_id and v_id != l_id:
                vassal_house = await self.session.get(House, v_id)
                if vassal_house:
                    vassal_house.liege_id = l_id
                    linked_count += 1

        # ====================================================
        # 🔄 PASS 3: FINALIZE ECONOMY
        # ====================================================
        await self.calculate_initial_treasury(game.game_id)
        await self.calculate_initial_manpower(game.game_id)

        await self.session.commit()

        return True, (
            f"✅ World Initialized ({era_mode})!\n"
            f"👑 Ruling House: **{ruling_house_name}**\n"
            f"🏰 Fiefs Created: {fiefs_created} | Links: {linked_count}"
        )

    async def calculate_initial_treasury(self, game_id: int):
        """
        Seeds the economy for the 'Fief + Purse' model.
        1. FIEFS: Start with 2 years of income (Local Treasury for recruiting).
        2. HOUSE: Starts with 1 year of total income (Personal Purse for politics).
        3. MANPOWER: Calculated from sum of fiefs.
        """

        # 1. Seed FIEF Treasuries (The Local Economy)
        # We give every castle 2x its income so players can use !buy immediately.
        stmt_fiefs = (
            update(Fief)
            .where((Fief.game_id == game_id) & (Fief.is_ruined == False))
            .values(treasury=Fief.base_income * 2)
        )
        await self.session.execute(stmt_fiefs)

        # 2. Seed HOUSE Treasury (The Lord's Purse)
        # We calculate the sum of all fief incomes and put 1x that amount
        # into the House 'purse'. This allows for gifts/bribes/transfers.
        income_subquery = (
            select(func.sum(Fief.base_income))
            .where((Fief.owner_id == House.house_id) & (Fief.is_ruined == False))
            .correlate(House)
            .scalar_subquery()
        )

        stmt_houses = (
            update(House)
            .where(House.game_id == game_id)
            .values(treasury=func.coalesce(income_subquery, 0))  # 1x Income as Reserve
        )
        await self.session.execute(stmt_houses)

    async def calculate_initial_manpower(self, game_id: int):
        """Sets starting manpower pools."""
        manpower_subquery = (
            select(func.sum(Fief.base_manpower))
            .where(Fief.owner_id == House.house_id)
            .correlate(House)
            .scalar_subquery()
        )
        stmt = (
            update(House)
            .where(House.game_id == game_id)
            .values(
                manpower=func.coalesce(manpower_subquery, 0),
                manpower_cap=func.coalesce(manpower_subquery, 0),
            )
        )
        await self.session.execute(stmt)
