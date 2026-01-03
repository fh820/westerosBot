import random
import json  # Explicitly import json as it's used in cargo parsing
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.models import (
    House,
    User,
    GamePlayer,
    Character,
    Fief,
    Army,
)  # Import Army as it's used
import datetime


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

        # LINK THE CHARACTER TO THE PLAYER
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
            liege_id=parent_house.house_id,
            house_type="faction",
            treasury=0,
            color_hex=parent_house.color_hex,
            # Courtiers don't pay taxes until they are granted land
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
    #     Fetches dashboard data.
    #     For regular players, it fetches their own house data.
    #     For GMs, it fetches a list of dashboards for all houses in the game.
    #     """
    #     # 0. Get the User object to check if they are a GM
    #     user_stmt = select(User).where(User.discord_id == discord_id)
    #     user = (await self.session.execute(user_stmt)).scalars().first()

    #     if not user:
    #         return None, "❌ User not found."

    #     # 1. Build a fast, in-memory "phonebook" to look up fief names from coordinates.
    #     all_fiefs_result = await self.session.execute(
    #         select(Fief).where(Fief.game_id == game_id)
    #     )
    #     game_fief_map = {
    #         (int(f.location_x), int(f.location_y)): f.name
    #         for f in all_fiefs_result.scalars().all()
    #     }

    #     # Helper function to generate a single house's dashboard data
    #     async def _generate_house_dashboard(
    #         target_house: House, is_player_claimed: bool = False
    #     ):
    #         # Re-fetch the house with its relations loaded, just in case `target_house`
    #         # came from a different query that didn't load them.
    #         loaded_house_stmt = (
    #             select(House)
    #             .where(House.house_id == target_house.house_id)
    #             .options(
    #                 selectinload(House.fiefs),
    #                 selectinload(House.armies),
    #                 selectinload(House.dynasty),
    #             )
    #         )
    #         loaded_house = (
    #             (await self.session.execute(loaded_house_stmt)).scalars().first()
    #         )

    #         if not loaded_house:
    #             return None  # Should not happen if target_house was valid

    #         house_char: Character | None = None
    #         if is_player_claimed:
    #             # If claimed by player, try to find the specific character linked to the primary player
    #             player_for_house = await self.session.scalar(
    #                 select(GamePlayer)
    #                 .where(
    #                     GamePlayer.game_id == game_id,
    #                     GamePlayer.claimed_house_id == loaded_house.house_id,
    #                     GamePlayer.is_primary == True,
    #                 )
    #                 .options(selectinload(GamePlayer.character))
    #             )
    #             if player_for_house:
    #                 house_char = player_for_house.character

    #         # If not player-claimed or player character not found, default to head character if available
    #         if not house_char:
    #             house_char_stmt = select(Character).where(
    #                 Character.house_id == loaded_house.house_id,
    #                 Character.is_head == True,
    #             )
    #             house_char = (
    #                 (await self.session.execute(house_char_stmt)).scalars().first()
    #             )

    #         total_income = sum(
    #             f.base_income * f.integration for f in loaded_house.fiefs
    #         )
    #         total_troops = sum(a.troop_count for a in loaded_house.armies)

    #         armies_data = []
    #         for army in loaded_house.armies:
    #             curr_key = (int(army.location_x), int(army.location_y))
    #             loc_name = game_fief_map.get(curr_key, f"{curr_key[0]}, {curr_key[1]}")

    #             destination_name = None
    #             if (
    #                 army.status in ["MARCHING", "SAILING"]
    #                 and army.destination_x is not None
    #             ):
    #                 dest_key = (int(army.destination_x), int(army.destination_y))
    #                 destination_name = game_fief_map.get(
    #                     dest_key, f"{dest_key[0]}, {dest_key[1]}"
    #                 )

    #             cargo_count = 0
    #             if army.cargo:
    #                 if isinstance(army.cargo, dict):
    #                     cargo_count = army.cargo.get("troop_count", 0)
    #                 elif isinstance(army.cargo, str):
    #                     try:
    #                         loaded = json.loads(army.cargo)
    #                         cargo_count = loaded.get("troop_count", 0)
    #                     except:
    #                         pass

    #             armies_data.append(
    #                 {
    #                     "id": army.army_id,
    #                     "name": army.commander_name,
    #                     "type": army.army_type,
    #                     "count": army.troop_count,
    #                     "comp": army.composition,
    #                     "location": loc_name,
    #                     "status": army.status,
    #                     "destination": destination_name,
    #                     "cargo_count": cargo_count,
    #                 }
    #             )

    #         is_scion = loaded_house.house_type == "faction"
    #         parent_name = (
    #             loaded_house.dynasty.name if is_scion and loaded_house.dynasty else None
    #         )

    #         return {
    #             "name": (
    #                 house_char.name if house_char else loaded_house.name
    #             ),  # Character name if available, else house name
    #             "house_id": loaded_house.house_id,  # Added for GM context
    #             "house_name": loaded_house.name,
    #             "parent_house": parent_name,
    #             "color": loaded_house.color_hex,
    #             "treasury": loaded_house.treasury,
    #             "income": total_income,
    #             "fiefs": [f.name for f in loaded_house.fiefs],
    #             "total_troops": total_troops,
    #             "armies": armies_data,
    #             "skills": house_char.skills if house_char else {},
    #             "is_primary_player_house": is_player_claimed,  # New flag
    #             "manpower": loaded_house.manpower,
    #             "manpower_cap": loaded_house.manpower_cap,
    #         }

    #     # --- GM DASHBOARD LOGIC ---
    #     if user.is_gm:  # Assuming User.is_gm field exists
    #         all_houses_in_game_stmt = (
    #             select(House)
    #             .where(House.game_id == game_id)
    #             .options(
    #                 selectinload(House.fiefs),
    #                 selectinload(House.armies),
    #                 selectinload(House.dynasty),
    #             )
    #         )
    #         all_houses = (
    #             (await self.session.execute(all_houses_in_game_stmt)).scalars().all()
    #         )

    #         all_player_claimed_house_ids = set()
    #         player_stmt = select(GamePlayer.claimed_house_id).where(
    #             GamePlayer.game_id == game_id, GamePlayer.is_primary == True
    #         )
    #         player_results = await self.session.execute(player_stmt)
    #         all_player_claimed_house_ids.update(player_results.scalars().all())

    #         gm_dashboards = []
    #         for house in all_houses:
    #             is_claimed = house.house_id in all_player_claimed_house_ids
    #             dashboard_data = await _generate_house_dashboard(
    #                 house, is_player_claimed=is_claimed
    #             )
    #             if dashboard_data:
    #                 gm_dashboards.append(dashboard_data)

    #         # Return a list of dashboards for the GM
    #         return gm_dashboards, None

    #     # --- REGULAR PLAYER DASHBOARD LOGIC (Original logic) ---
    #     else:
    #         player_stmt = (
    #             select(GamePlayer)
    #             .where(
    #                 GamePlayer.game_id == game_id,
    #                 GamePlayer.user_id == user.user_id,
    #             )
    #             .options(
    #                 selectinload(GamePlayer.house).selectinload(House.fiefs),
    #                 selectinload(GamePlayer.house).selectinload(House.armies),
    #                 selectinload(GamePlayer.character),
    #                 selectinload(GamePlayer.house).selectinload(House.dynasty),
    #             )
    #         )
    #         player = (await self.session.execute(player_stmt)).scalars().first()

    #         if not player or not player.house:
    #             return None, "❌ You have not claimed a role yet."

    #         # Use the helper function for consistency
    #         dashboard_data = await _generate_house_dashboard(
    #             player.house, is_player_claimed=True
    #         )
    #         if dashboard_data:
    #             # Add player-specific info if needed, e.g., if player has multiple characters
    #             # For now, player.is_primary is already handled by _generate_house_dashboard's is_player_claimed
    #             return dashboard_data, None
    #         else:
    #             return None, "❌ Could not generate dashboard for your house."

    # async def get_house_dashboard(self, game_id: int, house_name: str):
    #     """
    #     Fetches the detailed dashboard for a single house, specified by name.
    #     Intended for GM use.
    #     """
    #     # 1. Find the house by name
    #     stmt = select(House).where(
    #         House.game_id == game_id, House.name.ilike(house_name)
    #     )
    #     house = (await self.session.execute(stmt)).scalars().first()

    #     if not house:
    #         return None, f"❌ House '{house_name}' not found in this game."

    #     # 2. Check if the house is claimed by a player
    #     player_stmt = select(GamePlayer.claimed_house_id).where(
    #         GamePlayer.game_id == game_id,
    #         GamePlayer.claimed_house_id == house.house_id,
    #         GamePlayer.is_primary == True,
    #     )
    #     is_claimed = (
    #         await self.session.execute(player_stmt)
    #     ).scalars().first() is not None

    #     # 3. Generate and return the dashboard using the existing helper
    #     # We need the full game_fief_map for this to work, so we build it here.
    #     all_fiefs_result = await self.session.execute(
    #         select(Fief).where(Fief.game_id == game_id)
    #     )
    #     game_fief_map = {
    #         (int(f.location_x), int(f.location_y)): f.name
    #         for f in all_fiefs_result.scalars().all()
    #     }

    #     # The _generate_house_dashboard helper needs to be defined within this class
    #     # (It was previously nested inside get_player_dashboard, so we should make it a proper method)
    #     # We will assume you've moved _generate_house_dashboard to be a method of GameplayService

    #     # Let's redefine the helper here for clarity and self-containment of this example.
    #     # In your actual code, you should make _generate_house_dashboard a method of the class.
    #     async def _generate_house_dashboard(
    #         target_house: House, is_player_claimed: bool = False
    #     ):
    #         # This is the same helper function as before
    #         loaded_house_stmt = (
    #             select(House)
    #             .where(House.house_id == target_house.house_id)
    #             .options(
    #                 selectinload(House.fiefs),
    #                 selectinload(House.armies),
    #                 selectinload(House.dynasty),
    #             )
    #         )
    #         loaded_house = (
    #             (await self.session.execute(loaded_house_stmt)).scalars().first()
    #         )

    #         if not loaded_house:
    #             return None

    #         house_char: Character | None = None
    #         if is_player_claimed:
    #             player_for_house = await self.session.scalar(
    #                 select(GamePlayer)
    #                 .where(
    #                     GamePlayer.game_id == game_id,
    #                     GamePlayer.claimed_house_id == loaded_house.house_id,
    #                     GamePlayer.is_primary == True,
    #                 )
    #                 .options(selectinload(GamePlayer.character))
    #             )
    #             if player_for_house:
    #                 house_char = player_for_house.character
    #         else:
    #             house_char_stmt = select(Character).where(
    #                 Character.house_id == loaded_house.house_id,
    #                 Character.is_head == True,
    #             )
    #             house_char = (
    #                 (await self.session.execute(house_char_stmt)).scalars().first()
    #             )

    #         total_income = sum(
    #             f.base_income * f.integration for f in loaded_house.fiefs
    #         )
    #         total_troops = sum(a.troop_count for a in loaded_house.armies)

    #         armies_data = []
    #         for army in loaded_house.armies:
    #             curr_key = (int(army.location_x), int(army.location_y))
    #             loc_name = game_fief_map.get(curr_key, f"{curr_key[0]}, {curr_key[1]}")

    #             destination_name = None
    #             if (
    #                 army.status in ["MARCHING", "SAILING"]
    #                 and army.destination_x is not None
    #             ):
    #                 dest_key = (int(army.destination_x), int(army.destination_y))
    #                 destination_name = game_fief_map.get(
    #                     dest_key, f"{dest_key[0]}, {dest_key[1]}"
    #                 )

    #             cargo_count = 0
    #             if army.cargo:
    #                 if isinstance(army.cargo, dict):
    #                     cargo_count = army.cargo.get("troop_count", 0)
    #                 elif isinstance(army.cargo, str):
    #                     try:
    #                         loaded = json.loads(army.cargo)
    #                         cargo_count = loaded.get("troop_count", 0)
    #                     except:
    #                         pass

    #             armies_data.append(
    #                 {
    #                     "id": army.army_id,
    #                     "name": army.commander_name,
    #                     "type": army.army_type,
    #                     "count": army.troop_count,
    #                     "comp": army.composition,
    #                     "location": loc_name,
    #                     "status": army.status,
    #                     "destination": destination_name,
    #                     "cargo_count": cargo_count,
    #                 }
    #             )

    #         is_scion = loaded_house.house_type == "faction"
    #         parent_name = (
    #             loaded_house.dynasty.name if is_scion and loaded_house.dynasty else None
    #         )

    #         return {
    #             "name": (house_char.name if house_char else loaded_house.name),
    #             "house_id": loaded_house.house_id,
    #             "house_name": loaded_house.name,
    #             "parent_house": parent_name,
    #             "color": loaded_house.color_hex,
    #             "treasury": loaded_house.treasury,
    #             "income": total_income,
    #             "fiefs": [f.name for f in loaded_house.fiefs],
    #             "total_troops": total_troops,
    #             "armies": armies_data,
    #             "skills": house_char.skills if house_char else {},
    #             "is_primary_player_house": is_claimed,
    #             "manpower": loaded_house.manpower,
    #             "manpower_cap": loaded_house.manpower_cap,
    #         }

    #     dashboard_data = await _generate_house_dashboard(
    #         house, is_player_claimed=is_claimed
    #     )

    #     return dashboard_data, None

    # In your GameplayService class...

    async def get_player_dashboard(self, game_id: int, discord_id: int):
        """
        Fetches dashboard data.
        For regular players, it fetches their own house data.
        For GMs, it fetches a list of dashboards for all houses in the game.
        """
        user_stmt = select(User).where(User.discord_id == discord_id)
        user = (await self.session.execute(user_stmt)).scalars().first()
        if not user:
            return None, "❌ User not found."

        all_fiefs_result = await self.session.execute(
            select(Fief).where(Fief.game_id == game_id)
        )
        game_fief_map = {
            (int(f.location_x), int(f.location_y)): f.name
            for f in all_fiefs_result.scalars().all()
        }

        async def _generate_house_dashboard(
            target_house: House, is_player_claimed: bool = False
        ):
            loaded_house_stmt = (
                select(House)
                .where(House.house_id == target_house.house_id)
                .options(
                    selectinload(House.fiefs),
                    selectinload(House.armies),
                    selectinload(House.dynasty),
                )
            )
            loaded_house = (
                (await self.session.execute(loaded_house_stmt)).scalars().first()
            )
            if not loaded_house:
                return None

            house_char: Character | None = None
            if is_player_claimed:
                player_for_house = await self.session.scalar(
                    select(GamePlayer)
                    .where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.claimed_house_id == loaded_house.house_id,
                        GamePlayer.is_primary == True,
                    )
                    .options(selectinload(GamePlayer.character))
                )
                if player_for_house:
                    house_char = player_for_house.character

            if not house_char:
                house_char_stmt = select(Character).where(
                    Character.house_id == loaded_house.house_id,
                    Character.is_head == True,
                )
                house_char = (
                    (await self.session.execute(house_char_stmt)).scalars().first()
                )

            # --- GHOST ARMY CORRECTION LOGIC [START] ---
            now = datetime.datetime.now(datetime.timezone.utc)
            hidden_ghosts_map = {}
            ids_to_hide = set()

            for army in loaded_house.armies:
                if army.status == "MARCHING" and army.departure_time:
                    dep_time = army.departure_time
                    if dep_time.tzinfo is None:
                        dep_time = dep_time.replace(tzinfo=datetime.timezone.utc)
                    if dep_time > now:
                        ids_to_hide.add(army.army_id)
                        fleet_arrival_key = (
                            int(army.location_x),
                            int(army.location_y),
                            army.departure_time,
                        )
                        hidden_ghosts_map[fleet_arrival_key] = {
                            "troop_count": army.troop_count
                        }
            # --- GHOST ARMY CORRECTION LOGIC [END] ---

            total_income = sum(
                f.base_income * f.integration for f in loaded_house.fiefs
            )
            # Correctly calculate total troops by excluding hidden armies
            total_troops = sum(
                a.troop_count
                for a in loaded_house.armies
                if a.army_id not in ids_to_hide
            )

            armies_data = []
            # Filter the main army list to hide the ghosts
            for army in [
                a for a in loaded_house.armies if a.army_id not in ids_to_hide
            ]:
                curr_key = (int(army.location_x), int(army.location_y))
                loc_name = game_fief_map.get(curr_key, f"{curr_key[0]}, {curr_key[1]}")

                destination_name = None
                if (
                    army.status in ["MARCHING", "SAILING"]
                    and army.destination_x is not None
                ):
                    dest_key = (int(army.destination_x), int(army.destination_y))
                    destination_name = game_fief_map.get(
                        dest_key, f"{dest_key[0]}, {dest_key[1]}"
                    )

                cargo_count = 0
                if army.cargo and isinstance(army.cargo, dict):
                    cargo_count = army.cargo.get("troop_count", 0)

                # --- GHOST ARMY CORRECTION LOGIC [START] ---
                # If this fleet is transporting a hidden ghost, set its cargo count for display
                if army.army_type == "SEA" and army.status == "SAILING":
                    fleet_key = (
                        int(army.destination_x),
                        int(army.destination_y),
                        army.arrival_time,
                    )
                    if fleet_key in hidden_ghosts_map:
                        cargo_count = hidden_ghosts_map[fleet_key]["troop_count"]
                # --- GHOST ARMY CORRECTION LOGIC [END] ---

                armies_data.append(
                    {
                        "id": army.army_id,
                        "name": army.commander_name,
                        "type": army.army_type,
                        "count": army.troop_count,
                        "comp": army.composition,
                        "location": loc_name,
                        "status": army.status,
                        "destination": destination_name,
                        "cargo_count": cargo_count,
                    }
                )

            is_scion = loaded_house.house_type == "faction"
            parent_name = (
                loaded_house.dynasty.name if is_scion and loaded_house.dynasty else None
            )

            return {
                "name": (house_char.name if house_char else loaded_house.name),
                "house_id": loaded_house.house_id,
                "house_name": loaded_house.name,
                "parent_house": parent_name,
                "color": loaded_house.color_hex,
                "treasury": loaded_house.treasury,
                "income": total_income,
                "fiefs": [f.name for f in loaded_house.fiefs],
                "total_troops": total_troops,
                "armies": armies_data,
                "skills": house_char.skills if house_char else {},
                "is_primary_player_house": is_player_claimed,
                "manpower": loaded_house.manpower,
                "manpower_cap": loaded_house.manpower_cap,
            }

        # GM and Player logic remains the same, but will now use the corrected helper function.
        if user.is_gm:
            all_houses_in_game_stmt = select(House).where(House.game_id == game_id)
            all_houses = (
                (await self.session.execute(all_houses_in_game_stmt)).scalars().all()
            )
            player_stmt = select(GamePlayer.claimed_house_id).where(
                GamePlayer.game_id == game_id, GamePlayer.is_primary == True
            )
            player_results = await self.session.execute(player_stmt)
            all_player_claimed_house_ids = set(player_results.scalars().all())

            gm_dashboards = []
            for house in all_houses:
                is_claimed = house.house_id in all_player_claimed_house_ids
                dashboard_data = await _generate_house_dashboard(
                    house, is_player_claimed=is_claimed
                )
                if dashboard_data:
                    gm_dashboards.append(dashboard_data)
            return gm_dashboards, None
        else:
            player_stmt = (
                select(GamePlayer)
                .where(
                    GamePlayer.game_id == game_id,
                    GamePlayer.user_id == user.user_id,
                )
                .options(selectinload(GamePlayer.house))
            )
            player = (await self.session.execute(player_stmt)).scalars().first()

            if not player or not player.house:
                return None, "❌ You have not claimed a role yet."

            dashboard_data = await _generate_house_dashboard(
                player.house, is_player_claimed=True
            )
            return (
                (dashboard_data, None)
                if dashboard_data
                else (None, "❌ Could not generate dashboard.")
            )

    async def get_house_dashboard(self, game_id: int, house_name: str):
        """
        Fetches the detailed dashboard for a single house, specified by name.
        Intended for GM use.
        (This function is also updated to ensure GMs see the correct army states).
        """
        stmt = select(House).where(
            House.game_id == game_id, House.name.ilike(house_name)
        )
        house = (await self.session.execute(stmt)).scalars().first()
        if not house:
            return None, f"❌ House '{house_name}' not found in this game."

        player_stmt = select(GamePlayer.claimed_house_id).where(
            GamePlayer.game_id == game_id,
            GamePlayer.claimed_house_id == house.house_id,
            GamePlayer.is_primary == True,
        )
        is_claimed = (
            await self.session.execute(player_stmt)
        ).scalars().first() is not None

        all_fiefs_result = await self.session.execute(
            select(Fief).where(Fief.game_id == game_id)
        )
        game_fief_map = {
            (int(f.location_x), int(f.location_y)): f.name
            for f in all_fiefs_result.scalars().all()
        }

        # This nested helper is a duplicate from the function above, so we apply the same fix.
        # For future improvements, this could be refactored into a single private class method.
        async def _generate_house_dashboard(
            target_house: House, is_player_claimed: bool = False
        ):
            loaded_house_stmt = (
                select(House)
                .where(House.house_id == target_house.house_id)
                .options(
                    selectinload(House.fiefs),
                    selectinload(House.armies),
                    selectinload(House.dynasty),
                )
            )
            loaded_house = (
                (await self.session.execute(loaded_house_stmt)).scalars().first()
            )
            if not loaded_house:
                return None

            house_char: Character | None = None
            if is_player_claimed:
                player_for_house = await self.session.scalar(
                    select(GamePlayer)
                    .where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.claimed_house_id == loaded_house.house_id,
                        GamePlayer.is_primary == True,
                    )
                    .options(selectinload(GamePlayer.character))
                )
                if player_for_house:
                    house_char = player_for_house.character

            if not house_char:
                house_char_stmt = select(Character).where(
                    Character.house_id == loaded_house.house_id,
                    Character.is_head == True,
                )
                house_char = (
                    (await self.session.execute(house_char_stmt)).scalars().first()
                )

            # --- GHOST ARMY CORRECTION LOGIC [START] ---
            now = datetime.datetime.now(datetime.timezone.utc)
            hidden_ghosts_map = {}
            ids_to_hide = set()

            for army in loaded_house.armies:
                if army.status == "MARCHING" and army.departure_time:
                    dep_time = army.departure_time
                    if dep_time.tzinfo is None:
                        dep_time = dep_time.replace(tzinfo=datetime.timezone.utc)
                    if dep_time > now:
                        ids_to_hide.add(army.army_id)
                        fleet_arrival_key = (
                            int(army.location_x),
                            int(army.location_y),
                            army.departure_time,
                        )
                        hidden_ghosts_map[fleet_arrival_key] = {
                            "troop_count": army.troop_count
                        }
            # --- GHOST ARMY CORRECTION LOGIC [END] ---

            total_income = sum(
                f.base_income * f.integration for f in loaded_house.fiefs
            )
            total_troops = sum(
                a.troop_count
                for a in loaded_house.armies
                if a.army_id not in ids_to_hide
            )

            armies_data = []
            for army in [
                a for a in loaded_house.armies if a.army_id not in ids_to_hide
            ]:
                curr_key = (int(army.location_x), int(army.location_y))
                loc_name = game_fief_map.get(curr_key, f"{curr_key[0]}, {curr_key[1]}")

                destination_name = None
                if (
                    army.status in ["MARCHING", "SAILING"]
                    and army.destination_x is not None
                ):
                    dest_key = (int(army.destination_x), int(army.destination_y))
                    destination_name = game_fief_map.get(
                        dest_key, f"{dest_key[0]}, {dest_key[1]}"
                    )

                cargo_count = 0
                if army.cargo and isinstance(army.cargo, dict):
                    cargo_count = army.cargo.get("troop_count", 0)

                # --- GHOST ARMY CORRECTION LOGIC [START] ---
                if army.army_type == "SEA" and army.status == "SAILING":
                    fleet_key = (
                        int(army.destination_x),
                        int(army.destination_y),
                        army.arrival_time,
                    )
                    if fleet_key in hidden_ghosts_map:
                        cargo_count = hidden_ghosts_map[fleet_key]["troop_count"]
                # --- GHOST ARMY CORRECTION LOGIC [END] ---

                armies_data.append(
                    {
                        "id": army.army_id,
                        "name": army.commander_name,
                        "type": army.army_type,
                        "count": army.troop_count,
                        "comp": army.composition,
                        "location": loc_name,
                        "status": army.status,
                        "destination": destination_name,
                        "cargo_count": cargo_count,
                    }
                )

            is_scion = loaded_house.house_type == "faction"
            parent_name = (
                loaded_house.dynasty.name if is_scion and loaded_house.dynasty else None
            )

            return {
                "name": (house_char.name if house_char else loaded_house.name),
                "house_id": loaded_house.house_id,
                "house_name": loaded_house.name,
                "parent_house": parent_name,
                "color": loaded_house.color_hex,
                "treasury": loaded_house.treasury,
                "income": total_income,
                "fiefs": [f.name for f in loaded_house.fiefs],
                "total_troops": total_troops,
                "armies": armies_data,
                "skills": house_char.skills if house_char else {},
                "is_primary_player_house": is_claimed,
                "manpower": loaded_house.manpower,
                "manpower_cap": loaded_house.manpower_cap,
            }

        dashboard_data = await _generate_house_dashboard(
            house, is_player_claimed=is_claimed
        )
        return dashboard_data, None
