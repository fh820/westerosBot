import random
import datetime
from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload
from app.db.repositories import ArmyRepo, FiefRepo
from app.db.models import (
    House,
    GamePlayer,
    Army,
    User,
    Fief,
    Character,
    Game,
    PendingBannerCall,
)
from app.services.travel_calculator import calculate_travel_duration, format_duration
from app.tasks.heavy_tasks import process_banner_call
import os
from app.services.engine_manager import PF_ENGINE
import math
from sqlalchemy.orm.attributes import flag_modified


class DiplomacyService:
    def __init__(self, session):
        self.session = session

    async def execute_muster_from_pending_call(self, pending_call_id: int) -> list:
        """
        Final execution step for LAND or SEA levies.
        Muster troops/ships based on GM-approved percentages and issue movement orders.
        """
        pending_call = await self.session.get(PendingBannerCall, pending_call_id)
        if not pending_call or pending_call.status != "PENDING_APPROVAL":
            return []

        game = await self.session.get(Game, pending_call.game_id)

        # 1. Resolve Rally Point
        from app.services.warfare_service import WarfareService

        war_service = WarfareService(self.session)
        rally_coords_dict = await war_service._get_location_from_db(
            game.game_id, pending_call.rally_point_name
        )

        if not rally_coords_dict:
            return [
                f"❌ **Error:** Rally point '{pending_call.rally_point_name}' is invalid."
            ]

        target_coords = (rally_coords_dict["x"], rally_coords_dict["y"])
        liege_house_id = pending_call.liege_house_id
        march_results = []

        gm_settings = {
            "twins_open": game.twins_open,
            "rubyford_open": game.rubyford_open,
            "bitterbridge_open": game.bitterbridge_open,
            "rivers_impassable": game.rivers_impassable,
            "sea_travel_allowed": game.sea_travel_allowed,
        }

        # 2. Process Approved Vassals
        for vassal in pending_call.vassal_data:
            house_id = vassal["house_id"]
            house_name = vassal["house_name"]

            # Standardize based on call type
            max_val = (
                vassal.get("max_ships")
                if pending_call.call_type == "SEA"
                else vassal.get("max_troops")
            )
            # Handle standard keys from prepare_banner_call update
            if max_val is None:
                max_val = vassal.get("max_amount", 0)

            amount = int(max_val * vassal.get("percent", 0.0))

            if amount <= 0:
                march_results.append(f"🍂 **{house_name}** is sending no forces.")
                continue

            start_coords = (
                float(vassal.get("home_x", 0)),
                float(vassal.get("home_y", 0)),
            )
            if start_coords == (0, 0):
                march_results.append(
                    f"⚠️ **{house_name}** skipped (Unknown coordinates)."
                )
                continue

            # 3. Locate Source Army (Garrison or Fleet)
            stmt_army = select(Army).where(
                Army.game_id == game.game_id,
                Army.house_id == house_id,
                Army.status.in_(["GARRISONED", "DOCKED", "IDLE"]),
                Army.army_type == pending_call.call_type,
            )
            candidates = (await self.session.execute(stmt_army)).scalars().all()

            found_army = None
            for cand in candidates:
                # Basic proximity check to ensure we aren't pulling a garrison from across the world
                if (
                    math.sqrt(
                        (cand.location_x - start_coords[0]) ** 2
                        + (cand.location_y - start_coords[1]) ** 2
                    )
                    < 5.0
                ):
                    found_army = cand
                    break

            if not found_army:
                march_results.append(
                    f"⚠️ **{house_name}**: No valid host found at home."
                )
                continue

            # 4. Draft and Route
            # Land ratio vs Sea ships
            if pending_call.call_type == "SEA":
                source_comp = {"ships": amount}
            else:
                source_comp = {
                    "infantry": int(amount * 0.6),
                    "cavalry": int(amount * 0.25),
                    "archers": int(amount * 0.15),
                }

            if found_army.troop_count > amount:
                found_army.troop_count -= amount
                flag_modified(found_army, "composition")
            else:
                amount = found_army.troop_count
                source_comp = found_army.composition.copy()
                await self.session.delete(found_army)

            travel_mode = "sea_only" if pending_call.call_type == "SEA" else "land_only"
            journey = await PF_ENGINE.find_journey_async(
                start_loc=start_coords,
                end_loc=target_coords,
                gm_settings=gm_settings,
                travel_mode=travel_mode,
            )

            if journey:
                dur = calculate_travel_duration(journey["terrain_breakdown"], amount)
                arrival = datetime.datetime.now(
                    datetime.timezone.utc
                ) + datetime.timedelta(seconds=dur)

                new_levy = Army(
                    game_id=game.game_id,
                    house_id=liege_house_id,
                    army_type=pending_call.call_type,
                    commander_name=f"Levy of {house_name}",
                    troop_count=amount,
                    composition=source_comp,
                    location_x=start_coords[0],
                    location_y=start_coords[1],
                    destination_x=target_coords[0],
                    destination_y=target_coords[1],
                    status="SAILING" if travel_mode == "sea_only" else "MARCHING",
                    arrival_time=arrival,
                    departure_time=datetime.datetime.now(datetime.timezone.utc),
                )
                self.session.add(new_levy)
                await self.session.flush()

                from app.tasks.light_tasks import resolve_army_arrival

                new_levy.task_id = resolve_army_arrival.apply_async(
                    args=[new_levy.army_id], eta=arrival
                ).id
                march_results.append(
                    f"✅ **{house_name}** (Levy) is moving to rally point. ETA: **{int(dur/3600)}h**."
                )
            else:
                march_results.append(
                    f"⚠️ **{house_name}** raised but stuck (No Path found)."
                )

        pending_call.status = "COMPLETED"
        await self.session.commit()
        return march_results

    async def prepare_banner_call(
        self, game_id, liege_discord_id=None, acting_house_id=None, is_gm_override=False
    ):
        """Phase 1: Gather Land Vassals and their Locked Channel IDs recursively."""
        liege_house, liege_player = await self._get_caller_context(
            game_id, liege_discord_id, acting_house_id, is_gm_override
        )
        if not liege_house:
            return False, "❌ House not found.", []

        # Landed Lord Check
        stmt_land = select(Fief.fief_id).where(Fief.owner_id == liege_house.house_id)
        if (
            not (await self.session.execute(stmt_land)).scalars().first()
            and not is_gm_override
        ):
            return False, "❌ You have no land and therefore no banners to call.", []

        async def process_house_tree(house_obj):
            # Check for Player Owner and fetch locked ID
            stmt_owner = (
                select(User.discord_id, Character.name, GamePlayer.private_channel_id)
                .join(GamePlayer)
                .outerjoin(Character)
                .where(
                    GamePlayer.game_id == game_id,
                    GamePlayer.claimed_house_id == house_obj.house_id,
                )
            )
            owner_res = (await self.session.execute(stmt_owner)).first()

            if owner_res:
                disc_id, char_name, chan_id = owner_res
                # Filter out landless courtiers
                v_land = (
                    (
                        await self.session.execute(
                            select(Fief.fief_id).where(
                                Fief.owner_id == house_obj.house_id
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if not v_land:
                    return 0, [], []

                return (
                    0,
                    [],
                    [
                        {
                            "house_name": house_obj.name,
                            "character_name": char_name,
                            "user_id": disc_id,
                            "private_channel_id": chan_id,  # LOCKED ID
                        }
                    ],
                )

            # NPC Calculation
            stmt_a = select(func.sum(Army.troop_count)).where(
                Army.house_id == house_obj.house_id,
                Army.army_type == "LAND",
                Army.status.in_(["GARRISONED", "IDLE"]),
            )
            tree_troops = (await self.session.execute(stmt_a)).scalar() or 0

            tree_breakdown, tree_notifs = [], []
            stmt_sub = select(House).where(
                House.liege_id == house_obj.house_id, House.is_ruined == False
            )
            for sub in (await self.session.execute(stmt_sub)).scalars().all():
                s_troops, s_names, s_notifs = await process_house_tree(sub)
                tree_troops += s_troops
                tree_breakdown.extend(s_names if s_troops > 0 else [])
                tree_notifs.extend(s_notifs)

            return tree_troops, tree_breakdown, tree_notifs

        # Execute walk
        npc_data, player_notifs = [], []
        dip_stat = (
            int(liege_player.character.skills.get("diplomacy", 10))
            if liege_player and liege_player.character
            else 10
        )

        stmt_direct = select(House).where(
            House.liege_id == liege_house.house_id, House.is_ruined == False
        )
        for vassal in (await self.session.execute(stmt_direct)).scalars().all():
            troops, breakdown, notifs = await process_house_tree(vassal)
            player_notifs.extend(notifs)
            if troops > 0:
                stmt_home = (
                    select(Fief)
                    .where(Fief.owner_id == vassal.house_id)
                    .order_by(Fief.base_income.desc())
                    .limit(1)
                )
                home = (await self.session.execute(stmt_home)).scalars().first()

                score = 30 + (dip_stat * 2) + (10 if liege_house.treasury > 5000 else 0)
                npc_data.append(
                    {
                        "house_id": vassal.house_id,
                        "house_name": vassal.name,
                        "max_amount": troops,
                        "percent": (
                            0.9
                            if score >= 80
                            else 0.7 if score >= 60 else 0.3 if score >= 40 else 0.0
                        ),
                        "home_x": home.location_x if home else 0,
                        "home_y": home.location_y if home else 0,
                        "breakdown": ", ".join(breakdown),
                    }
                )

        return True, npc_data, player_notifs

    async def disband_levies(self, game_id: int, liege_user_id: int):
        """
        Finds all armies named 'X Levy' commanded by the player,
        deletes them, and returns troops to the vassal's capital.
        """
        # 1. Get Liege
        stmt_p = select(GamePlayer).where(
            GamePlayer.user_id == liege_user_id, GamePlayer.game_id == game_id
        )
        player = (await self.session.execute(stmt_p)).scalars().first()
        if not player or not player.claimed_house_id:
            return False, "❌ No house found."

        # 2. Find Levies (Armies owned by Liege ending in 'Levy')
        stmt_a = select(Army).where(
            Army.game_id == game_id,
            Army.house_id == player.claimed_house_id,
            Army.commander_name.like("% Levy"),
        )
        levies = (await self.session.execute(stmt_a)).scalars().all()

        if not levies:
            return False, "❌ You have no active levies to disband."

        report = []

        for levy in levies:
            # Parse House Name from "Glover Levy"
            vassal_name = levy.commander_name.replace(" Levy", "")

            # Find Vassal House
            stmt_h = select(House).where(
                House.game_id == game_id, House.name == vassal_name
            )
            vassal_house = (await self.session.execute(stmt_h)).scalars().first()

            if not vassal_house:
                # Edge case: House destroyed? Just delete army.
                await self.session.delete(levy)
                continue

            # Find a place to return them (Ideally their capital/first fief)
            stmt_f = select(Fief).where(Fief.owner_id == vassal_house.house_id).limit(1)
            home_fief = (await self.session.execute(stmt_f)).scalars().first()

            if home_fief:
                # Find or Create Garrison at home
                stmt_g = select(Army).where(
                    Army.house_id == vassal_house.house_id,
                    Army.location_x == home_fief.location_x,
                    Army.location_y == home_fief.location_y,
                    Army.status == "GARRISONED",
                )
                garrison = (await self.session.execute(stmt_g)).scalars().first()

                if garrison:
                    # Merge troops back
                    garrison.troop_count += levy.troop_count
                    # Simple merge of composition
                    for unit, count in levy.composition.items():
                        garrison.composition[unit] = (
                            garrison.composition.get(unit, 0) + count
                        )
                    # Force update JSON
                    from sqlalchemy.orm.attributes import flag_modified

                    flag_modified(garrison, "composition")
                else:
                    # Create new Garrison if it was empty
                    new_garrison = Army(
                        game_id=game_id,
                        house_id=vassal_house.house_id,
                        commander_name=f"Garrison of {home_fief.name}",
                        troop_count=levy.troop_count,
                        composition=levy.composition,
                        location_x=home_fief.location_x,
                        location_y=home_fief.location_y,
                        status="GARRISONED",
                        army_type="LAND",
                    )
                    self.session.add(new_garrison)

                report.append(
                    f"↩️ **{levy.commander_name}** ({levy.troop_count}) returned to **{home_fief.name}**."
                )

            # Delete the Levy Army object
            await self.session.delete(levy)

        await self.session.commit()
        return True, report

    def _get_proficiency_bonus(self, level: int) -> int:
        if level <= 4:
            return 2
        if level <= 8:
            return 3
        if level <= 12:
            return 4
        if level <= 16:
            return 5
        return 6

    async def _get_caller_context(
        self,
        game_id: int,
        liege_discord_id: int | None = None,
        acting_house_id: int | None = None,
        is_gm_override: bool = False,
    ):
        """
        Helper to unify liege/caller identification logic for both land and sea calls.
        Returns: (House, GamePlayer)
        """
        liege_house: House | None = None
        liege_player: GamePlayer | None = None

        if is_gm_override and acting_house_id is not None:
            # GM is acting on behalf of a specific house
            liege_house = await self.session.get(House, acting_house_id)
            if not liege_house:
                return None, None

            # Create a mock player object so diplomacy/skill checks don't crash
            liege_player = GamePlayer(
                game_id=game_id,
                user_id=0,
                claimed_house_id=acting_house_id,
                is_primary=True,
            )
            liege_player.house = liege_house

        elif liege_discord_id is not None:
            # Standard player call
            stmt_p = (
                select(GamePlayer)
                .join(User, GamePlayer.user_id == User.user_id)
                .where(
                    User.discord_id == liege_discord_id, GamePlayer.game_id == game_id
                )
                .options(
                    selectinload(GamePlayer.house), selectinload(GamePlayer.character)
                )
            )
            liege_player = (await self.session.execute(stmt_p)).scalars().first()
            if liege_player:
                liege_house = liege_player.house

        return liege_house, liege_player

    async def prepare_sea_levy_call(
        self, game_id, liege_discord_id=None, acting_house_id=None, is_gm_override=False
    ):
        """Phase 1: Gather Sea Vassals and their Locked Channel IDs recursively."""
        liege_house, liege_player = await self._get_caller_context(
            game_id, liege_discord_id, acting_house_id, is_gm_override
        )
        if not liege_house:
            return False, "❌ House not found.", []

        # Ownership Check (Must have ships or coastal land)
        stmt_land = select(Fief.fief_id).where(Fief.owner_id == liege_house.house_id)
        if (
            not (await self.session.execute(stmt_land)).scalars().first()
            and not is_gm_override
        ):
            return False, "❌ You have no fleets to call.", []

        async def process_sea_tree(house_obj):
            # Check for Player Owner and fetch locked ID
            stmt_owner = (
                select(User.discord_id, Character.name, GamePlayer.private_channel_id)
                .join(GamePlayer)
                .outerjoin(Character)
                .where(
                    GamePlayer.game_id == game_id,
                    GamePlayer.claimed_house_id == house_obj.house_id,
                )
            )
            owner_res = (await self.session.execute(stmt_owner)).first()

            if owner_res:
                disc_id, char_name, chan_id = owner_res
                # Filter players with no navy
                v_ships = (
                    (
                        await self.session.execute(
                            select(Army.army_id).where(
                                Army.house_id == house_obj.house_id,
                                Army.army_type == "SEA",
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if not v_ships:
                    return 0, [], [], None

                return (
                    0,
                    [],
                    [
                        {
                            "house_name": house_obj.name,
                            "character_name": char_name,
                            "user_id": disc_id,
                            "private_channel_id": chan_id,  # LOCKED ID
                        }
                    ],
                    None,
                )

            # NPC Calculation: Find Largest Fleet
            stmt_f = (
                select(Army)
                .where(
                    Army.house_id == house_obj.house_id,
                    Army.army_type == "SEA",
                    Army.status.in_(["IDLE", "DOCKED", "GARRISONED"]),
                )
                .order_by(Army.troop_count.desc())
                .limit(1)
            )
            main_fleet = (await self.session.execute(stmt_f)).scalars().first()

            tree_ships = main_fleet.troop_count if main_fleet else 0
            tree_breakdown, tree_notifs = [], []
            fleet_id = main_fleet.army_id if main_fleet else None

            stmt_sub = select(House).where(
                House.liege_id == house_obj.house_id, House.is_ruined == False
            )
            for sub in (await self.session.execute(stmt_sub)).scalars().all():
                s_ships, s_names, s_notifs, _ = await process_sea_tree(sub)
                tree_ships += s_ships
                tree_breakdown.extend(s_names if s_ships > 0 else [])
                tree_notifs.extend(s_notifs)

            return tree_ships, tree_breakdown, tree_notifs, fleet_id

        # Execute walk
        npc_data, player_notifs = [], []
        dip_stat = (
            int(liege_player.character.skills.get("diplomacy", 10))
            if liege_player and liege_player.character
            else 10
        )

        stmt_direct = select(House).where(
            House.liege_id == liege_house.house_id, House.is_ruined == False
        )
        for vassal in (await self.session.execute(stmt_direct)).scalars().all():
            ships, breakdown, notifs, f_id = await process_sea_tree(vassal)
            player_notifs.extend(notifs)
            if ships > 0:
                # Sea rally starts from the fleet's current location
                stmt_loc = select(Army.location_x, Army.location_y).where(
                    Army.army_id == f_id
                )
                loc = (await self.session.execute(stmt_loc)).first()

                score = 30 + (dip_stat * 2) + (10 if liege_house.treasury > 5000 else 0)
                npc_data.append(
                    {
                        "house_id": vassal.house_id,
                        "house_name": vassal.name,
                        "max_amount": ships,
                        "percent": (
                            0.9
                            if score >= 80
                            else 0.7 if score >= 60 else 0.3 if score >= 40 else 0.0
                        ),
                        "home_x": loc[0] if loc else 0,
                        "home_y": loc[1] if loc else 0,
                        "source_fleet_id": f_id,
                        "breakdown": ", ".join(breakdown),
                    }
                )

        return True, npc_data, player_notifs

    async def check_marriage_authority(
        self, arranger_player: GamePlayer, subject_char: Character
    ) -> bool:
        """
        Checks if the arranger has the authority to marry off the subject character.
        - A player can always arrange for their own characters.
        - A Head of House can arrange for any NPC in their house.
        """
        # First, see if the subject character is claimed by any player
        stmt_subject_player = select(GamePlayer).where(
            GamePlayer.character_id == subject_char.char_id
        )
        subject_player = (
            (await self.session.execute(stmt_subject_player)).scalars().first()
        )

        if subject_player:
            # The subject IS a player character. The arranger must BE that player.
            return subject_player.user_id == arranger_player.user_id
        else:
            # The subject is an NPC. The arranger must be the Head of that NPC's House.
            return (
                arranger_player.is_primary
                and subject_char.house_id == arranger_player.claimed_house_id
            )

    async def find_consenting_player(self, character: Character) -> GamePlayer | None:
        """
        Finds the GAME PLAYER who must consent for a given character. This is a critical
        piece of logic for social commands.

        The hierarchy of consent is:
        1. The player who has explicitly claimed the specific character.
        2. If the character is an NPC (unclaimed), the player who is the Head of that character's House.
        3. If the Head of House is also an NPC or un-claimed, returns None (requires GM approval).
        """
        # Eagerly load all relationships we might need to prevent extra queries
        options = [
            selectinload(GamePlayer.user),
            selectinload(GamePlayer.house),
            selectinload(GamePlayer.character),
        ]

        # Case 1: Is there a player who has explicitly claimed THIS character?
        # This is the highest priority for consent.
        stmt_char_player = (
            select(GamePlayer)
            .where(GamePlayer.character_id == character.char_id)
            .options(*options)
        )
        char_player = (await self.session.execute(stmt_char_player)).scalars().first()

        # If we found a player and that player is not an NPC bot, they are the consenter.
        if char_player and char_player.user and not char_player.user.is_npc:
            return char_player

        # Case 2: The character is an NPC. Consent falls to the Head of their House.
        # Find the player who is the primary claimant (Head of House) for the character's house.
        stmt_hoh = (
            select(GamePlayer)
            .where(
                GamePlayer.claimed_house_id == character.house_id,
                GamePlayer.is_primary == True,
            )
            .options(*options)
        )
        hoh_player = (await self.session.execute(stmt_hoh)).scalars().first()

        # If we found a Head of House and they are not an NPC bot, they are the consenter.
        if hoh_player and hoh_player.user and not hoh_player.user.is_npc:
            return hoh_player

        # Case 3: The character is an NPC, and their House is also run by an NPC or is unclaimed.
        # No player can consent, so GM approval is required.
        return None

    async def execute_marriage(self, game_id: int, char_name_a: str, char_name_b: str):
        """
        Finalizes a marriage between two characters, creating them if they do not exist.
        This is the core logic that updates the database.
        """
        # Use the helper to find or create the characters
        char_a = await self.find_or_create_char(game_id, char_name_a)
        char_b = await self.find_or_create_char(game_id, char_name_b)

        # Validation
        if not char_a:
            return (
                False,
                f"❌ Could not find or create a character for '{char_name_a}'. Ensure their House exists in the game.",
            )
        if not char_b:
            return (
                False,
                f"❌ Could not find or create a character for '{char_name_b}'. Ensure their House exists in the game.",
            )
        if char_a.char_id == char_b.char_id:
            return False, "❌ A character cannot marry themselves."
        if char_a.spouse_id or char_b.spouse_id:
            return False, "❌ One of the characters is already married."

        # Core Logic: Link the two characters via their spouse_id
        char_a.spouse_id = char_b.char_id
        char_b.spouse_id = char_a.char_id

        await self.session.commit()

        return (
            True,
            f"Let it be known that **{char_a.name}** and **{char_b.name}** have been joined in matrimony!",
        )

    async def declare_fealty(
        self,
        game_id: int,
        new_liege_name: str,
        vassal_user_id: int | None = None,
        vassal_house_id: int | None = None,
        is_gm_override: bool = False,
    ):
        """
        Updates the liege_id of a house. Now supports GM override.
        """
        vassal_house: House | None = None

        if is_gm_override and vassal_house_id is not None:
            vassal_house = await self.session.get(House, vassal_house_id)
            if not vassal_house:
                return False, f"❌ NPC House ID {vassal_house_id} not found."
        elif vassal_user_id is not None:
            stmt_p = select(GamePlayer).where(
                GamePlayer.user_id == vassal_user_id, GamePlayer.game_id == game_id
            )
            player = (await self.session.execute(stmt_p)).scalars().first()

            if not player or not player.claimed_house_id:
                return False, "❌ You do not command a house."
            vassal_house = await self.session.get(House, player.claimed_house_id)
        else:
            return False, "❌ No vassal house identified."

        stmt_l = select(House).where(
            House.game_id == game_id, House.name.ilike(new_liege_name)
        )
        liege_house = (await self.session.execute(stmt_l)).scalars().first()

        if not liege_house:
            return False, f"❌ House **{new_liege_name}** not found."
        if liege_house.house_id == vassal_house.house_id:
            return False, "❌ A house cannot swear fealty to itself."
        if vassal_house.liege_id == liege_house.house_id:
            return (
                False,
                f"ℹ️ **House {vassal_house.name}** already bends the knee to **{liege_house.name}**.",
            )

        vassal_house.liege_id = liege_house.house_id
        await self.session.commit()
        return (
            True,
            f"📜 **Fealty Sworn:** House **{vassal_house.name}** now bends the knee to **{liege_house.name}**.",
        )
