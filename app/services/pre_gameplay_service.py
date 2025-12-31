import random
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.models import House, User, GamePlayer, Character, Fief


class GameplayService:
    def __init__(self, session):
        self.session = session

    def roll_5d20(self):
        """Generates the standard 5d20 stat block."""
        return {
            "diplomacy": random.randint(1, 20),
            "martial": random.randint(1, 20),
            "stewardship": random.randint(1, 20),
            "intrigue": random.randint(1, 20),
            "prowess": random.randint(1, 20),
        }

    async def claim_house(self, game_id: int, discord_id: int, house_name: str):
        """
        Handles the logic of a user claiming a primary house.
        """
        # 1. Find House
        stmt = select(House).where(
            House.game_id == game_id, House.name.ilike(house_name)
        )
        house = (await self.session.execute(stmt)).scalars().first()
        if not house:
            return False, f"❌ House **{house_name}** not found.", None

        # 2. Check if already claimed
        stmt_claimed = select(GamePlayer).where(
            GamePlayer.claimed_house_id == house.house_id, GamePlayer.is_primary == True
        )
        if (await self.session.execute(stmt_claimed)).scalars().first():
            return False, f"❌ House **{house.name}** is already claimed.", None

        # 3. Create/Get User
        stmt_user = select(User).where(User.discord_id == discord_id)
        user = (await self.session.execute(stmt_user)).scalars().first()
        if not user:
            user = User(discord_id=discord_id, is_npc=False)
            self.session.add(user)
            await self.session.flush()

        # 4. Check if User already plays
        stmt_player = select(GamePlayer).where(
            GamePlayer.user_id == user.user_id, GamePlayer.game_id == game_id
        )
        if (await self.session.execute(stmt_player)).scalars().first():
            return False, "❌ You have already claimed a role in this game!", None

        # 5. Create GamePlayer Link (Primary)
        player = GamePlayer(
            game_id=game_id,
            user_id=user.user_id,
            claimed_house_id=house.house_id,
            is_primary=True,
        )
        self.session.add(player)
        await self.session.flush()  # Flush to get player ID

        # 6. Character Generation (Head of House)
        stmt_char = select(Character).where(
            Character.house_id == house.house_id, Character.is_head == True
        )
        head_char = (await self.session.execute(stmt_char)).scalars().first()

        stats_msg = ""
        if not head_char:
            skills = self.roll_5d20()
            head_char = Character(
                house_id=house.house_id,
                name=f"Lord of {house.name}",
                is_head=True,
                skills=skills,
            )
            self.session.add(head_char)
            await self.session.flush()  # Get char ID
            stats_msg = (
                f"\n🎲 **Stats Rolled (5d20):**\n"
                f"⚔️ Martial: {skills['martial']}\n📜 Diplomacy: {skills['diplomacy']}\n"
                f"💰 Stewardship: {skills['stewardship']}\n👁️ Intrigue: {skills['intrigue']}\n"
                f"💪 Prowess: {skills['prowess']}"
            )

        # 🚨 THE FIX: LINK THE CHARACTER TO THE PLAYER 🚨
        player.character_id = head_char.char_id

        await self.session.commit()

        return (
            True,
            f"✅ **Claim Successful!** You are now the head of **House {house.name}**.{stats_msg}",
            house,
        )

    async def validate_claim_request(
        self, game_id: int, discord_id: int, input_string: str
    ):
        """
        Smart Validation:
        1. Checks for exact House match ("Stark")
        2. Checks for Parent House match ("Sansa Stark" -> Parent "Stark")
        """
        claim_type = "House"

        # A. Try Finding Exact House
        stmt = select(House).where(
            House.game_id == game_id, House.name.ilike(input_string)
        )
        result = await self.session.execute(stmt)
        house = result.scalars().first()

        # B. If not found, Try Finding Parent House (Character Claim logic)
        if not house:
            parts = input_string.split(" ")
            if len(parts) > 1:
                parent_name = parts[-1]  # Assumption: Last word is House (e.g. "Stark")
                stmt_parent = select(House).where(
                    House.game_id == game_id, House.name.ilike(parent_name)
                )
                res_parent = await self.session.execute(stmt_parent)
                house = res_parent.scalars().first()
                if house:
                    claim_type = "Character"

        # C. If still nothing, Fail
        if not house:
            return (
                False,
                f"❌ Could not find House or Parent House for **{input_string}**.",
                None,
            )

        # D. Check Availability
        if claim_type == "House":
            # Direct House Claim: Must be empty
            stmt_claimed = select(GamePlayer).where(
                GamePlayer.claimed_house_id == house.house_id,
                GamePlayer.is_primary == True,
            )
            if (await self.session.execute(stmt_claimed)).scalars().first():
                return (
                    False,
                    f"❌ House **{house.name}** is already ruled by another player.",
                    None,
                )
        else:
            # Character Claim: Check if this specific character Faction already exists
            char_name = " ".join(input_string.split(" ")[:-1])
            stmt_fac = select(House).where(
                House.game_id == game_id, House.name.ilike(char_name)
            )
            if (await self.session.execute(stmt_fac)).scalars().first():
                return False, f"❌ **{char_name}** is already played.", None

        # E. Check User Status (One Role Per Game)
        stmt_user = select(User).where(User.discord_id == discord_id)
        user_obj = (await self.session.execute(stmt_user)).scalars().first()

        if user_obj:
            stmt_player = select(GamePlayer).where(
                GamePlayer.user_id == user_obj.user_id, GamePlayer.game_id == game_id
            )
            if (await self.session.execute(stmt_player)).scalars().first():
                return False, "❌ You already have a role in this game.", None

        return True, "Valid", house

    async def claim_character(
        self, game_id: int, discord_id: int, full_claim_string: str
    ):
        """
        Creates a 'Personal House' (Faction) for a character.
        Robust Parser: Handles "Arthur Dayne of Starfall" correctly.
        """
        parts = full_claim_string.split(" ")
        if len(parts) < 2:
            return (
                False,
                "❌ Invalid format. Use `!claim [Name] [ParentHouse]`",
                None,
                None,
            )

        # --- SMART PARSING LOOP ---
        # Try to find a valid House by combining words from the end
        # Example: "Arthur Dayne of Starfall"
        # 1. Try "Starfall" -> Fail
        # 2. Try "Dayne of Starfall" -> Success!

        parent_house = None
        char_name = ""

        # We iterate backwards from 1 word to (length-1) words
        for i in range(1, len(parts)):
            potential_house_name = " ".join(parts[-i:])  # Get last i words

            stmt = select(House).where(
                House.game_id == game_id, House.name.ilike(potential_house_name)
            )
            result = await self.session.execute(stmt)
            parent_house = result.scalars().first()

            if parent_house:
                # Found it! The rest of the string is the character name
                char_name = " ".join(parts[:-i])
                break

        if not parent_house:
            # Fallback for error message (assume last word was intended house)
            return (
                False,
                f"❌ Parent House **{parts[-1]}** (or variations) not found.",
                None,
                None,
            )

        # --------------------------------------------------
        # The rest of the logic is identical to before...
        # --------------------------------------------------

        # 2. Check if Faction Exists
        stmt_check = select(House).where(
            House.game_id == game_id, House.name.ilike(char_name)
        )
        if (await self.session.execute(stmt_check)).scalars().first():
            return False, f"❌ **{char_name}** is already claimed.", None, None

        # 3. Create/Get User
        stmt_u = select(User).where(User.discord_id == discord_id)
        user = (await self.session.execute(stmt_u)).scalars().first()
        if not user:
            user = User(discord_id=discord_id)
            self.session.add(user)
            await self.session.flush()

        # 4. Check User Role
        stmt_p = select(GamePlayer).where(
            GamePlayer.user_id == user.user_id, GamePlayer.game_id == game_id
        )
        if (await self.session.execute(stmt_p)).scalars().first():
            return False, "❌ You already have a role in this game.", None, None

        # 5. Create Faction House
        faction = House(
            game_id=game_id,
            name=char_name,
            dynasty_id=parent_house.house_id,
            house_type="faction",
            treasury=0,
            color_hex=parent_house.color_hex,
            paying_taxes=False,
        )
        self.session.add(faction)
        await self.session.flush()

        # 6. Create Character
        skills = self.roll_5d20()
        char_obj = Character(
            house_id=faction.house_id, name=char_name, is_head=True, skills=skills
        )
        self.session.add(char_obj)
        await self.session.flush()

        # 7. Create Player
        player = GamePlayer(
            game_id=game_id,
            user_id=user.user_id,
            claimed_house_id=faction.house_id,
            character_id=char_obj.char_id,
        )
        self.session.add(player)
        await self.session.commit()

        # Format Stats Message
        stats_msg = (
            f"\n🎲 **Stats Rolled (5d20):**\n"
            f"⚔️ Martial: {skills['martial']}\n"
            f"📜 Diplomacy: {skills['diplomacy']}\n"
            f"💰 Stewardship: {skills['stewardship']}\n"
            f"👁️ Intrigue: {skills['intrigue']}\n"
            f"💪 Prowess: {skills['prowess']}"
        )

        return (
            True,
            f"✅ You are now **{char_name}** (Scion of {parent_house.name}).{stats_msg}",
            faction,
            parent_house,
        )

    # async def get_player_dashboard(self, game_id: int, discord_id: int):
    #     """
    #     Fetches all data for the !me command.
    #     """
    #     # 1. Get Player & Linked Data
    #     stmt = (
    #         select(GamePlayer)
    #         .where(
    #             GamePlayer.game_id == game_id,
    #             GamePlayer.user_id
    #             == (
    #                 select(User.user_id)
    #                 .where(User.discord_id == discord_id)
    #                 .scalar_subquery()
    #             ),
    #         )
    #         .options(
    #             selectinload(GamePlayer.house).selectinload(House.fiefs),
    #             selectinload(GamePlayer.house).selectinload(House.armies),
    #             selectinload(GamePlayer.character),
    #             selectinload(GamePlayer.house).selectinload(House.dynasty),
    #         )
    #     )
    #     result = await self.session.execute(stmt)
    #     player = result.scalars().first()

    #     if not player or not player.house:
    #         return None, "❌ You have not claimed a role yet."

    #     house = player.house
    #     char = player.character

    #     # 2. Calculations
    #     total_income = sum(f.base_income * f.integration for f in house.fiefs)
    #     total_troops = sum(a.troop_count for a in house.armies)

    #     fief_map = {(f.location_x, f.location_y): f.name for f in house.fiefs}
    #     armies_data = []
    #     for army in house.armies:
    #         loc_name = fief_map.get((army.location_x, army.location_y), "In the Field")
    #         destination_name = None
    #         if army.status == "MARCHING":
    #             loc_name = "En Route"  # A better name for a marching army's location
    #             destination_coords = (army.destination_x, army.destination_y)
    #             # Look up the destination name in our universal map
    #             destination_name = game_fief_map.get(
    #                 destination_coords,
    #                 f"Coords: {destination_coords[0]},{destination_coords[1]}",
    #             )

    #         armies_data.append(
    #             {
    #                 "id": army.army_id,
    #                 "name": army.commander_name,
    #                 "count": army.troop_count,
    #                 "comp": army.composition,
    #                 "location": loc_name,
    #                 "status": army.status,
    #             }
    #         )

    #     is_scion = house.house_type == "faction"
    #     parent_name = house.dynasty.name if is_scion and house.dynasty else None

    #     # 3. Final Data Structure
    #     data = {
    #         "name": char.name if char else house.name,
    #         "house_name": house.name,
    #         "parent_house": parent_name,
    #         "color": house.color_hex,
    #         "treasury": house.treasury,
    #         "income": total_income,
    #         "fiefs": [f.name for f in house.fiefs],
    #         "total_troops": total_troops,
    #         "armies": armies_data,
    #         "skills": char.skills if char else {},
    #         "is_primary": player.is_primary,
    #         # 🚨 NEW: ADD MANPOWER TO RETURN DATA 🚨
    #         "manpower": house.manpower,
    #         "manpower_cap": house.manpower_cap,
    #     }

    #     return data, None

    async def get_player_dashboard(self, game_id: int, discord_id: int):
        """
        Fetches all data for the !me command, including optimized location lookups
        and robust cargo calculation for fleets.
        """
        # 1. Build a fast, in-memory "phonebook" to look up fief names from coordinates.
        # This avoids querying the DB inside the army loop.
        all_fiefs_result = await self.session.execute(
            select(Fief).where(Fief.game_id == game_id)
        )
        game_fief_map = {
            (int(f.location_x), int(f.location_y)): f.name
            for f in all_fiefs_result.scalars().all()
        }

        # 2. Get Player & Linked Data
        stmt = (
            select(GamePlayer)
            .where(
                GamePlayer.game_id == game_id,
                GamePlayer.user_id
                == (
                    select(User.user_id)
                    .where(User.discord_id == discord_id)
                    .scalar_subquery()
                ),
            )
            .options(
                selectinload(GamePlayer.house).selectinload(House.fiefs),
                selectinload(GamePlayer.house).selectinload(House.armies),
                selectinload(GamePlayer.character),
                selectinload(GamePlayer.house).selectinload(House.dynasty),
            )
        )
        player = (await self.session.execute(stmt)).scalars().first()

        if not player or not player.house:
            return None, "❌ You have not claimed a role yet."

        house = player.house
        char = player.character

        # 3. Calculations and Data Processing
        total_income = sum(f.base_income * f.integration for f in house.fiefs)
        total_troops = sum(a.troop_count for a in house.armies)

        armies_data = []
        for army in house.armies:
            # --- A. Determine Current Location Name ---
            # Try to match specific fief, otherwise show coordinates
            curr_key = (int(army.location_x), int(army.location_y))
            loc_name = game_fief_map.get(curr_key, f"{curr_key[0]}, {curr_key[1]}")

            # --- B. Determine Destination Name (If Moving) ---
            destination_name = None
            if (
                army.status in ["MARCHING", "SAILING"]
                and army.destination_x is not None
            ):
                dest_key = (int(army.destination_x), int(army.destination_y))
                destination_name = game_fief_map.get(
                    dest_key, f"{dest_key[0]}, {dest_key[1]}"
                )

            # --- C. Calculate Cargo (The Fix) ---
            # Robustly handles both Dictionary objects and JSON Strings
            cargo_count = 0
            if army.cargo:
                if isinstance(army.cargo, dict):
                    cargo_count = army.cargo.get("troop_count", 0)
                elif isinstance(army.cargo, str):
                    try:
                        import json

                        loaded = json.loads(army.cargo)
                        cargo_count = loaded.get("troop_count", 0)
                    except:
                        pass

            armies_data.append(
                {
                    "id": army.army_id,
                    "name": army.commander_name,
                    "count": army.troop_count,
                    "comp": army.composition,
                    "location": loc_name,
                    "status": army.status,
                    "destination": destination_name,
                    "cargo_count": cargo_count,  # <--- This triggers the 📦 icon
                }
            )

        is_scion = house.house_type == "faction"
        parent_name = house.dynasty.name if is_scion and house.dynasty else None

        # 4. Final Data Structure
        data = {
            "name": char.name if char else house.name,
            "house_name": house.name,
            "parent_house": parent_name,
            "color": house.color_hex,
            "treasury": house.treasury,
            "income": total_income,
            "fiefs": [f.name for f in house.fiefs],
            "total_troops": total_troops,
            "armies": armies_data,
            "skills": char.skills if char else {},
            "is_primary": player.is_primary,
            "manpower": house.manpower,
            "manpower_cap": house.manpower_cap,
        }

        return data, None
