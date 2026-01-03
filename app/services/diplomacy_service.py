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


class DiplomacyService:
    def __init__(self, session):
        self.session = session

    async def execute_sea_muster_from_pending_call(self, pending_call_id: int) -> list:
        """
        The final execution step for naval levies. Takes a pending call, musters fleets
        based on GM-approved percentages, and returns the results for the player report.
        """
        pending_call = await self.session.get(PendingBannerCall, pending_call_id)
        if not pending_call or pending_call.status != "PENDING_APPROVAL":
            return []

        game = await self.session.get(Game, pending_call.game_id)
        rally_fief = await FiefRepo.get_by_name(
            self.session, game.game_id, pending_call.rally_point_name
        )
        if not rally_fief:
            pending_call.status = "CANCELLED"
            await self.session.commit()
            return [
                f"❌ Muster failed: Rally point '{pending_call.rally_point_name}' no longer exists."
            ]

        liege_house_id = pending_call.liege_house_id
        rally_coords = (rally_fief.location_x, rally_fief.location_y)

        sail_results = []
        gm_settings = {
            "twins_open": game.twins_open,
            "rubyford_open": game.rubyford_open,
            "bitterbridge_open": game.bitterbridge_open,
            "rivers_impassable": game.rivers_impassable,
        }

        for item in pending_call.vassal_data:
            house_id = item["house_id"]
            house_name = item["house_name"]
            ships_to_send = int(item["max_ships"] * item["percent"])

            if ships_to_send <= 0:
                sail_results.append(f"💨 {house_name} will not be sending ships.")
                continue

            sailing_fleet = await ArmyRepo.muster_fleet_from_garrison(
                session=self.session,
                game_id=game.game_id,
                liege_house_id=liege_house_id,
                vassal_house_id=house_id,
                ships_to_muster=ships_to_send,
                commander_name=f"Levy Fleet of {house_name}",
            )
            if not sailing_fleet:
                sail_results.append(
                    f"⚠️ {house_name} could not provide a fleet (not enough ships)."
                )
                continue

            journey_results = await PF_ENGINE.find_journey_async(
                start_loc=item["start_location"],
                end_loc=rally_coords,
                gm_settings=gm_settings,
                travel_mode="sea_only",
            )
            if journey_results:
                duration_seconds = calculate_travel_duration(
                    journey_results["terrain_breakdown"], ships_to_send
                )
                travel_time_hours = int(duration_seconds / 3600)
                now = datetime.datetime.now(datetime.timezone.utc)
                arrival_time = now + datetime.timedelta(seconds=duration_seconds)
                (
                    sailing_fleet.status,
                    sailing_fleet.destination_x,
                    sailing_fleet.destination_y,
                    sailing_fleet.arrival_time,
                    sailing_fleet.departure_time,
                ) = ("MARCHING", rally_coords[0], rally_coords[1], arrival_time, now)
                sail_results.append(
                    f"✅ **{sailing_fleet.commander_name}** ({ships_to_send} ships) is sailing to **{pending_call.rally_point_name}**. ETA: **{travel_time_hours} hours**."
                )
            else:
                sail_results.append(
                    f"⚠️ **{sailing_fleet.commander_name}** could not find a sea route to **{pending_call.rally_point_name}** and will remain docked."
                )

        pending_call.status = "COMPLETED"
        await self.session.commit()
        return sail_results

    # async def execute_muster_from_pending_call(self, pending_call_id: int):
    #     """
    #     The final execution step. Takes a pending call, musters armies based on GM-approved
    #     percentages, and returns the results for the player report.
    #     """
    #     pending_call = await self.session.get(PendingBannerCall, pending_call_id)
    #     if not pending_call or pending_call.status != "PENDING_APPROVAL":
    #         return []  # Or raise an error

    #     game = await self.session.get(Game, pending_call.game_id)
    #     rally_fief = await FiefRepo.get_by_name(
    #         self.session, game.game_id, pending_call.rally_point_name
    #     )
    #     liege_house_id = pending_call.liege_house_id

    #     march_results = []
    #     successful_etas_seconds = []
    #     valid_coord_vassals = []
    #     zero_coord_vassals = []
    #     gm_settings = {
    #         "twins_open": game.twins_open,
    #         "rubyford_open": game.rubyford_open,
    #         "bitterbridge_open": game.bitterbridge_open,
    #         "rivers_impassable": game.rivers_impassable,
    #     }

    #     # Use the GM-approved vassal_data from the pending call
    #     for vassal in pending_call.vassal_data:
    #         fief = await FiefRepo.get_main_fief_for_house(
    #             self.session, vassal["house_id"]
    #         )
    #         if fief and fief.location_x > 0 and fief.location_y > 0:
    #             valid_coord_vassals.append({"vassal_info": vassal, "fief": fief})
    #         else:
    #             zero_coord_vassals.append({"vassal_info": vassal, "fief": fief})

    #     # This logic is mostly moved from your original call_banners command
    #     for item in valid_coord_vassals:
    #         vassal_info, start_fief = item["vassal_info"], item["fief"]
    #         troops_to_send = int(vassal_info["max_troops"] * vassal_info["percent"])

    #         if troops_to_send <= 0:
    #             march_results.append(
    #                 f"🍂 {vassal_info['house_name']} has no available troops to send."
    #             )
    #             continue

    #         marching_army = await ArmyRepo.muster_from_garrison(
    #             session=self.session,
    #             game_id=game.game_id,
    #             owner_house_id=liege_house_id,
    #             source_house_id=vassal_info["house_id"],
    #             troops_to_muster=troops_to_send,
    #             commander_name=f"{vassal_info['house_name']} Levy",
    #         )
    #         if not marching_army:
    #             march_results.append(
    #                 f"⚠️ {vassal_info['house_name']} could not muster forces (no valid garrison found)."
    #             )
    #             continue

    #         journey_results = await PF_ENGINE.find_journey_async(
    #             start_loc=(start_fief.location_x, start_fief.location_y),
    #             end_loc=(rally_fief.location_x, rally_fief.location_y),
    #             gm_settings=gm_settings,
    #         )
    #         if journey_results:
    #             duration_seconds = calculate_travel_duration(
    #                 journey_results["terrain_breakdown"], troops_to_send
    #             )
    #             travel_time_hours = int(duration_seconds / 3600)
    #             now = datetime.datetime.now(datetime.timezone.utc)
    #             arrival_time = now + datetime.timedelta(seconds=duration_seconds)
    #             (
    #                 marching_army.status,
    #                 marching_army.destination_x,
    #                 marching_army.destination_y,
    #                 marching_army.arrival_time,
    #                 marching_army.departure_time,
    #             ) = (
    #                 "MARCHING",
    #                 rally_fief.location_x,
    #                 rally_fief.location_y,
    #                 arrival_time,
    #                 now,
    #             )
    #             successful_etas_seconds.append(duration_seconds)
    #             march_results.append(
    #                 f"✅ **{vassal_info['house_name']}** is marching to **{pending_call.rally_point_name}**. ETA: **{travel_time_hours} hours**."
    #             )
    #         else:
    #             march_results.append(
    #                 f"⚠️ The **{vassal_info['house_name']} Levy** could not find a route to **{pending_call.rally_point_name}** and will remain idle."
    #             )

    #     average_duration_seconds = (
    #         round(sum(successful_etas_seconds) / len(successful_etas_seconds))
    #         if successful_etas_seconds
    #         else 259200
    #     )
    #     average_eta_hours = int(average_duration_seconds / 3600)
    #     for item in zero_coord_vassals:
    #         vassal_info = item["vassal_info"]
    #         troops_to_send = int(vassal_info["max_troops"] * vassal_info["percent"])

    #         if troops_to_send <= 0:
    #             march_results.append(
    #                 f"🍂 {vassal_info['house_name']} has no available troops to send."
    #             )
    #             continue
    #         marching_army = await ArmyRepo.muster_from_garrison(
    #             session=self.session,
    #             game_id=game.game_id,
    #             owner_house_id=liege_house_id,
    #             source_house_id=vassal_info["house_id"],
    #             troops_to_muster=troops_to_send,
    #             commander_name=f"{vassal_info['house_name']} Levy",
    #         )
    #         if not marching_army:
    #             march_results.append(
    #                 f"⚠️ {vassal_info['house_name']} could not muster forces (no valid garrison found)."
    #             )
    #             continue
    #         now = datetime.datetime.now(datetime.timezone.utc)
    #         arrival_time = now + datetime.timedelta(seconds=average_duration_seconds)
    #         (
    #             marching_army.status,
    #             marching_army.destination_x,
    #             marching_army.destination_y,
    #             marching_army.arrival_time,
    #             marching_army.departure_time,
    #         ) = (
    #             "MARCHING",
    #             rally_fief.location_x,
    #             rally_fief.location_y,
    #             arrival_time,
    #             now,
    #         )
    #         march_results.append(
    #             f"✅ The **{vassal_info['house_name']} Levy** is mustering for **{pending_call.rally_point_name}**. ETA: **~{average_eta_hours} hours** (estimated)."
    #         )

    #     pending_call.status = "COMPLETED"
    #     await self.session.commit()

    #     return march_results

    async def execute_muster_from_pending_call(self, pending_call_id: int):
        """
        The final execution step.
        FIX: Generates correct composition for SEA units (Ships) vs LAND units (Troops).
        """
        pending_call = await self.session.get(PendingBannerCall, pending_call_id)
        if not pending_call or pending_call.status != "PENDING_APPROVAL":
            return []

        game = await self.session.get(Game, pending_call.game_id)

        # 1. RESOLVE RALLY POINT
        from app.services.warfare_service import WarfareService

        war_service = WarfareService(self.session)

        rally_coords_dict = await war_service._get_location_from_db(
            game.game_id, pending_call.rally_point_name
        )

        if not rally_coords_dict:
            return [
                f"❌ **Error:** The rally point '{pending_call.rally_point_name}' is invalid."
            ]

        target_x = rally_coords_dict["x"]
        target_y = rally_coords_dict["y"]

        # 2. SETUP
        liege_house_id = pending_call.liege_house_id
        march_results = []

        gm_settings = {
            "twins_open": game.twins_open,
            "rubyford_open": game.rubyford_open,
            "bitterbridge_open": game.bitterbridge_open,
            "rivers_impassable": game.rivers_impassable,
            "sea_travel_allowed": game.sea_travel_allowed,
        }

        from app.services.travel_calculator import calculate_travel_duration
        from app.services.engine_manager import PF_ENGINE
        import math
        import datetime

        # 3. PROCESS VASSALS
        for vassal in pending_call.vassal_data:
            house_id = vassal["house_id"]
            house_name = vassal["house_name"]

            max_val = (
                vassal.get("max_ships", 0)
                if pending_call.call_type == "SEA"
                else vassal.get("max_troops", 0)
            )
            percent = vassal.get("percent", 0.0)
            amount = int(max_val * percent)

            if amount <= 0:
                march_results.append(f"🍂 **{house_name}** is sending no forces.")
                continue

            start_x = float(vassal.get("home_x", 0))
            start_y = float(vassal.get("home_y", 0))

            if start_x == 0 and start_y == 0:
                march_results.append(
                    f"⚠️ **{house_name}** skipped (Unknown home location)."
                )
                continue

            # 4. LOCATE EXISTING ARMY
            search_statuses = ["GARRISONED", "DOCKED", "IDLE"]

            stmt_army = select(Army).where(
                Army.game_id == game.game_id,
                Army.house_id == house_id,
                Army.status.in_(search_statuses),
                Army.army_type == pending_call.call_type,
            )
            candidates = (await self.session.execute(stmt_army)).scalars().all()

            found_army = None
            for cand in candidates:
                if (
                    math.sqrt(
                        (cand.location_x - start_x) ** 2
                        + (cand.location_y - start_y) ** 2
                    )
                    > 2.0
                ):
                    continue

                if pending_call.call_type == "SEA":
                    # Exception for GARRISONED fleets (allowed on land/castles)
                    if cand.status == "GARRISONED":
                        found_army = cand
                        break

                    # Checks for DOCKED/IDLE fleets (Must be water/port + owned)
                    if not war_service._is_coord_water_or_port(
                        int(cand.location_x), int(cand.location_y)
                    ):
                        continue

                    stmt_port = select(Fief.owner_id).where(
                        Fief.game_id == game.game_id,
                        Fief.location_x == cand.location_x,
                        Fief.location_y == cand.location_y,
                    )
                    pid = (await self.session.execute(stmt_port)).scalar_one_or_none()
                    if pid not in [house_id, liege_house_id]:
                        continue

                found_army = cand
                break

            if not found_army:
                type_str = (
                    "garrison" if pending_call.call_type == "LAND" else "docked fleet"
                )
                march_results.append(
                    f"⚠️ **{house_name}**: Skipped (No valid {type_str} found at home)."
                )
                continue

            # --- DRAFT TROOPS (THE FIX) ---
            source_composition = {}

            if found_army.troop_count > amount:
                # Splitting the existing army
                found_army.troop_count -= amount

                # FIX: Generate correct composition type
                if pending_call.call_type == "SEA":
                    source_composition = {"ships": amount}
                else:
                    # Land Ratio
                    source_composition = {
                        "infantry": int(amount * 0.6),
                        "cavalry": int(amount * 0.25),
                        "archers": int(amount * 0.15),
                    }
            else:
                # Taking the whole army
                amount = found_army.troop_count
                source_composition = found_army.composition.copy()
                await self.session.delete(found_army)

            # 5. PATHFINDING
            travel_mode = (
                "land_only" if pending_call.call_type == "LAND" else "sea_only"
            )
            journey_results = await PF_ENGINE.find_journey_async(
                start_loc=(start_x, start_y),
                end_loc=(target_x, target_y),
                gm_settings=gm_settings,
                travel_mode=travel_mode,
            )

            if journey_results:
                duration_seconds = calculate_travel_duration(
                    journey_results["terrain_breakdown"], amount
                )
                travel_time_hours = int(duration_seconds / 3600)
                now = datetime.datetime.now(datetime.timezone.utc)
                arrival_time = now + datetime.timedelta(seconds=duration_seconds)

                new_levy = Army(
                    game_id=game.game_id,
                    house_id=liege_house_id,
                    army_type=pending_call.call_type,
                    commander_name=f"Levy of {house_name}",
                    troop_count=amount,
                    composition=source_composition,
                    location_x=start_x,
                    location_y=start_y,
                    destination_x=target_x,
                    destination_y=target_y,
                    status="MARCHING" if travel_mode == "land_only" else "SAILING",
                    departure_time=now,
                    arrival_time=arrival_time,
                )
                self.session.add(new_levy)
                await self.session.flush()

                from app.tasks.light_tasks import resolve_army_arrival

                task = resolve_army_arrival.apply_async(
                    args=[new_levy.army_id], eta=arrival_time
                )
                new_levy.task_id = task.id

                march_results.append(
                    f"✅ **{house_name}** (Levy) is moving to rally point. ETA: **{travel_time_hours}h**."
                )
            else:
                new_levy = Army(
                    game_id=game.game_id,
                    house_id=liege_house_id,
                    army_type=pending_call.call_type,
                    commander_name=f"Levy of {house_name}",
                    troop_count=amount,
                    composition=source_composition,
                    location_x=start_x,
                    location_y=start_y,
                    status="GARRISONED" if travel_mode == "land_only" else "DOCKED",
                )
                self.session.add(new_levy)
                march_results.append(f"⚠️ **{house_name}** raised but stuck (No Path).")

        # 6. CLEANUP
        pending_call.status = "COMPLETED"
        await self.session.commit()

        return march_results

    # async def prepare_banner_call(
    #     self,
    #     game_id: int,
    #     liege_discord_id: int | None = None,
    #     acting_house_id: int | None = None,
    #     is_gm_override: bool = False,
    # ):
    #     """
    #     Phase 1: Gathers vassal data.
    #     - SEPARATES Players (for notification) from NPCs (for automatic marching).
    #     - Now supports GM override to prepare a call for an NPC house.
    #     """
    #     # Determine the effective liege house based on GM override or player's discord_id
    #     liege_house: House | None = None
    #     liege_player: GamePlayer | None = None

    #     if is_gm_override and acting_house_id is not None:
    #         liege_house = await self.session.get(House, acting_house_id)
    #         if not liege_house:
    #             return False, [], []  # Specified NPC house not found
    #         # For GM override, create a dummy player object for authority checks
    #         liege_player = GamePlayer(
    #             game_id=game_id,
    #             user_id=0,
    #             claimed_house_id=acting_house_id,
    #             is_primary=True,
    #         )
    #         liege_player.house = liege_house  # Link the house object for access
    #     elif liege_discord_id is not None:
    #         stmt_p = (
    #             select(GamePlayer)
    #             .join(User, GamePlayer.user_id == User.user_id)
    #             .where(
    #                 User.discord_id == liege_discord_id,
    #                 GamePlayer.game_id == game_id,
    #                 GamePlayer.is_primary == True,
    #             )
    #             .options(
    #                 selectinload(GamePlayer.house), selectinload(GamePlayer.character)
    #             )
    #         )
    #         liege_player = (await self.session.execute(stmt_p)).scalars().first()
    #         if not liege_player or not liege_player.house:
    #             return False, [], []
    #         liege_house = liege_player.house
    #     else:
    #         return False, [], []  # No way to identify the liege

    #     # Get Diplomacy Stat (for NPC calculations)
    #     diplomacy_stat = 10
    #     if (
    #         liege_player.character and liege_player.character.skills
    #     ):  # Use liege_player's character
    #         diplomacy_stat = int(liege_player.character.skills.get("diplomacy", 10))
    #     # If liege_player is a dummy (GM override without character), default to 10.
    #     elif is_gm_override and not liege_player.character:
    #         diplomacy_stat = 10

    #     # Find all vassals
    #     stmt_v = select(House).where(
    #         House.game_id == game_id,
    #         House.liege_id == liege_house.house_id,
    #         House.is_ruined == False,
    #     )
    #     vassals = (await self.session.execute(stmt_v)).scalars().all()

    #     vassal_data = []  # List for NPCs (GM will approve these)
    #     player_vassals = []  # List for Players (Bot will DM these)

    #     # Process each vassal
    #     for v in vassals:
    #         # Check if this vassal house has a PLAYER owner
    #         stmt_owner = (
    #             select(GamePlayer)
    #             .join(User)
    #             .where(
    #                 GamePlayer.game_id == game_id,
    #                 GamePlayer.claimed_house_id == v.house_id,
    #                 GamePlayer.is_primary == True,
    #             )
    #             .options(selectinload(GamePlayer.user))
    #         )
    #         owner_player = (await self.session.execute(stmt_owner)).scalars().first()

    #         # --- BRANCH A: PLAYER VASSAL ---
    #         if owner_player and owner_player.user:
    #             player_vassals.append(
    #                 {
    #                     "house_name": v.name,
    #                     "user_id": owner_player.user.discord_id,
    #                     "house_id": v.house_id,
    #                 }
    #             )
    #             continue

    #         # --- BRANCH B: NPC VASSAL ---
    #         # Calculate troops for NPC
    #         stmt_a = select(func.sum(Army.troop_count)).where(
    #             Army.house_id == v.house_id,
    #             or_(Army.status == "GARRISONED", Army.status == "IDLE"),
    #         )
    #         total_troops = (await self.session.execute(stmt_a)).scalar() or 0

    #         # Calculate NPC Logic/Score
    #         score = 30 + (diplomacy_stat * 2)

    #         # Regional Bonus Check
    #         stmt_vf = select(Fief).where(Fief.owner_id == v.house_id).limit(1)
    #         stmt_lf = select(Fief).where(Fief.owner_id == liege_house.house_id).limit(1)
    #         vf = (await self.session.execute(stmt_vf)).scalars().first()
    #         lf = (await self.session.execute(stmt_lf)).scalars().first()

    #         if vf and lf and vf.region == lf.region:
    #             score += 30
    #         if liege_house.treasury > 5000:
    #             score += 10

    #         # Determine % of troops to send
    #         percent = 0.0
    #         if score >= 80:
    #             percent = 0.90
    #         elif score >= 60:
    #             percent = 0.70
    #         elif score >= 40:
    #             percent = 0.30

    #         if total_troops == 0:
    #             percent = 0.0

    #         vassal_data.append(
    #             {"house": v, "troops": total_troops, "percent": percent, "score": score}
    #         )

    #     return True, vassal_data, player_vassals

    async def prepare_banner_call(
        self,
        game_id: int,
        liege_discord_id: int | None = None,
        acting_house_id: int | None = None,
        is_gm_override: bool = False,
    ):
        """
        Phase 1: Gathers vassal data using RECURSIVE logic.
        - Aggregates NPC sub-vassals into the Direct Vassal's count.
        - Identifies Player sub-vassals for notification.
        - Returns 'max_amount' key to match the command expectation.
        """
        # 1. IDENTIFY LIEGE
        liege_house: House | None = None
        liege_player: GamePlayer | None = None

        if is_gm_override and acting_house_id is not None:
            liege_house = await self.session.get(House, acting_house_id)
            if not liege_house:
                return False, [], []
            liege_player = GamePlayer(
                game_id=game_id,
                user_id=0,
                claimed_house_id=acting_house_id,
                is_primary=True,
            )
            liege_player.house = liege_house
        elif liege_discord_id is not None:
            stmt_p = (
                select(GamePlayer)
                .join(User, GamePlayer.user_id == User.user_id)
                .where(
                    User.discord_id == liege_discord_id,
                    GamePlayer.game_id == game_id,
                    GamePlayer.is_primary == True,
                )
                .options(
                    selectinload(GamePlayer.house), selectinload(GamePlayer.character)
                )
            )
            liege_player = (await self.session.execute(stmt_p)).scalars().first()
            if not liege_player or not liege_player.house:
                return False, [], []
            liege_house = liege_player.house
        else:
            return False, [], []

        # 2. HELPER: RECURSIVE TREE WALKER
        # 2. HELPER: RECURSIVE TREE WALKER
        async def process_house_tree(house_obj):
            """
            Returns: (total_troops, breakdown_names_list, notifications_list)
            """
            # A. Check for Player Owner (JOIN Character to get the name for the quarters channel)
            stmt_owner = (
                select(User, Character.name)
                .join(GamePlayer, User.user_id == GamePlayer.user_id)
                .outerjoin(Character, GamePlayer.character_id == Character.char_id)
                .where(
                    GamePlayer.game_id == game_id,
                    GamePlayer.claimed_house_id == house_obj.house_id,
                    GamePlayer.is_primary == True,
                )
            )
            result = (await self.session.execute(stmt_owner)).first()

            if result:
                owner_user, char_name = result
                # Stop recursion for players.
                # IMPORTANT: Use 'user_id' and 'character_name' to match your Command logic
                return (
                    0,
                    [],
                    [
                        {
                            "house_name": house_obj.name,
                            "character_name": char_name,
                            "user_id": owner_user.discord_id,
                        }
                    ],
                )

            # B. NPC: Calculate Base Troops (Garrison + Idle)
            stmt_a = select(func.sum(Army.troop_count)).where(
                Army.house_id == house_obj.house_id,
                Army.army_type == "LAND",
                or_(Army.status == "GARRISONED", Army.status == "IDLE"),
            )
            base_troops = (await self.session.execute(stmt_a)).scalar() or 0

            # C. Recurse into Sub-Vassals
            stmt_sub = select(House).where(
                House.liege_id == house_obj.house_id,
                House.game_id == game_id,
                House.is_ruined == False,
            )
            sub_vassals = (await self.session.execute(stmt_sub)).scalars().all()

            tree_troops = base_troops
            tree_breakdown = []
            tree_notifs = []

            for sub in sub_vassals:
                s_troops, s_names, s_notifs = await process_house_tree(sub)

                if s_troops > 0:
                    tree_troops += s_troops
                    tree_breakdown.append(sub.name)

                tree_notifs.extend(s_notifs)

            return tree_troops, tree_breakdown, tree_notifs

        # 3. EXECUTE ON DIRECT VASSALS
        stmt_direct = select(House).where(
            House.game_id == game_id,
            House.liege_id == liege_house.house_id,
            House.is_ruined == False,
        )
        direct_vassals = (await self.session.execute(stmt_direct)).scalars().all()

        npc_data_list = []
        all_player_notifications = []

        # Diplomacy Stat for calculation
        diplomacy_stat = 10
        if liege_player.character and liege_player.character.skills:
            diplomacy_stat = int(liege_player.character.skills.get("diplomacy", 10))
        elif is_gm_override and not liege_player.character:
            diplomacy_stat = 10

        for vassal in direct_vassals:
            troops, breakdown_list, notifications = await process_house_tree(vassal)

            all_player_notifications.extend(notifications)

            # If troops > 0, it means the Direct Vassal is an NPC (or has NPC vassals contributing)
            if troops > 0:
                # Find Home Coordinates
                stmt_fief = (
                    select(Fief)
                    .where(Fief.owner_id == vassal.house_id, Fief.game_id == game_id)
                    .order_by(Fief.base_income.desc())
                    .limit(1)
                )

                home_fief = (await self.session.execute(stmt_fief)).scalars().first()
                h_x = home_fief.location_x if home_fief else 0.0
                h_y = home_fief.location_y if home_fief else 0.0

                # Score Logic
                score = 30 + (diplomacy_stat * 2)
                if liege_house.treasury > 5000:
                    score += 10

                percent = 0.0
                if score >= 80:
                    percent = 0.90
                elif score >= 60:
                    percent = 0.70
                elif score >= 40:
                    percent = 0.30

                # Name formatting
                display_name = vassal.name
                breakdown_str = ""
                if breakdown_list:
                    display_name += "*"
                    breakdown_str = ", ".join(breakdown_list)

                npc_data_list.append(
                    {
                        "house_id": vassal.house_id,
                        "house_name": display_name,
                        "max_amount": troops,  # <--- THIS FIXES YOUR KEYERROR
                        "percent": percent,
                        "home_x": h_x,
                        "home_y": h_y,
                        "breakdown": breakdown_str,
                    }
                )

        return True, npc_data_list, all_player_notifications

    # async def prepare_recursive_banner_call(
    #     self,
    #     game_id: int,
    #     liege_discord_id: int | None = None,
    #     acting_house_id: int | None = None,
    #     is_gm_override: bool = False,
    #     call_type: str = "LAND",  # "LAND" or "SEA"
    # ):
    #     """
    #     Robust Recursive Muster System.
    #     Handles nested vassal trees, player notifications deep in the tree,
    #     and aggregation of NPC sub-vassals into a combined levy.
    #     """
    #     # 1. IDENTIFY LIEGE
    #     liege_house = None
    #     if is_gm_override and acting_house_id:
    #         liege_house = await self.session.get(House, acting_house_id)
    #     elif liege_discord_id:
    #         stmt = (
    #             select(GamePlayer)
    #             .join(User)
    #             .where(
    #                 User.discord_id == liege_discord_id,
    #                 GamePlayer.game_id == game_id,
    #                 GamePlayer.is_primary == True,
    #             )
    #             .options(selectinload(GamePlayer.house))
    #         )
    #         player = (await self.session.execute(stmt)).scalars().first()
    #         if player:
    #             liege_house = player.house

    #     if not liege_house:
    #         return False, [], []

    #     # 2. HELPER: Recursive function to process a House
    #     # Returns: (troop_count_sum, breakdown_string, list_of_players_to_notify)
    #     async def process_house_tree(house_obj):
    #         # A. Check if this house is a PLAYER
    #         stmt_owner = (
    #             select(User)
    #             .join(GamePlayer)
    #             .where(
    #                 GamePlayer.claimed_house_id == house_obj.house_id,
    #                 GamePlayer.game_id == game_id,
    #                 GamePlayer.is_primary == True,
    #             )
    #         )
    #         player_user = (await self.session.execute(stmt_owner)).scalars().first()

    #         if player_user:
    #             # IT IS A PLAYER: Stop recursion here.
    #             # We return 0 troops (we don't steal them) and the player ID to notify.
    #             return 0, "", [{"name": house_obj.name, "id": player_user.discord_id}]

    #         # B. IT IS AN NPC: Calculate base troops
    #         base_troops = 0

    #         if call_type == "LAND":
    #             # Look for Garrison or Idle Land Armies
    #             stmt_troops = select(func.sum(Army.troop_count)).where(
    #                 Army.house_id == house_obj.house_id,
    #                 Army.army_type == "LAND",
    #                 Army.status.in_(["GARRISONED", "IDLE"]),
    #             )
    #             base_troops = (await self.session.execute(stmt_troops)).scalar() or 0

    #             # Logic: If no army exists, estimate manpower (optional fallback)
    #             # For strictness, we might keep it 0 if no garrison exists.
    #             # Let's assume we use the strict logic: 0 if no army.

    #         elif call_type == "SEA":
    #             # Look for Docked/Idle Fleets at owned ports
    #             # Simplified check for preparation phase
    #             stmt_ships = select(func.sum(Army.troop_count)).where(
    #                 Army.house_id == house_obj.house_id,
    #                 Army.army_type == "SEA",
    #                 Army.status.in_(["DOCKED", "IDLE"]),
    #             )
    #             base_troops = (await self.session.execute(stmt_ships)).scalar() or 0

    #         # C. RECURSE: Find vassals of this NPC
    #         stmt_vassals = select(House).where(
    #             House.liege_id == house_obj.house_id,
    #             House.game_id == game_id,
    #             House.is_ruined == False,
    #         )
    #         sub_vassals = (await self.session.execute(stmt_vassals)).scalars().all()

    #         total_tree_troops = base_troops
    #         tree_breakdown = []  # List of names contributed
    #         tree_notifications = []

    #         for sub in sub_vassals:
    #             s_troops, s_breakdown, s_notifs = await process_house_tree(sub)

    #             # Aggregate Logic
    #             if s_troops > 0:
    #                 total_tree_troops += s_troops
    #                 # We don't add full breakdown string, just the name to keep it clean
    #                 tree_breakdown.append(sub.name)

    #             tree_notifications.extend(s_notifs)

    #         # Format breakdown string for display
    #         final_breakdown_str = ""
    #         if tree_breakdown:
    #             final_breakdown_str = f" (Includes: {', '.join(tree_breakdown)})"

    #         return total_tree_troops, final_breakdown_str, tree_notifications

    #     # 3. EXECUTE ON DIRECT VASSALS
    #     stmt_direct = select(House).where(
    #         House.liege_id == liege_house.house_id,
    #         House.game_id == game_id,
    #         House.is_ruined == False,
    #     )
    #     direct_vassals = (await self.session.execute(stmt_direct)).scalars().all()

    #     npc_data_list = []
    #     all_player_notifications = []

    #     for vassal in direct_vassals:
    #         # We call the recursive function on the direct vassal
    #         troops, breakdown, notifications = await process_house_tree(vassal)

    #         # Collect player notifications found anywhere in this branch
    #         all_player_notifications.extend(notifications)

    #         # If the direct vassal ended up returning troops, it means it's an NPC (or tree of NPCs)
    #         if troops > 0:
    #             # Get Coordinates
    #             stmt_fief = (
    #                 select(Fief)
    #                 .where(Fief.owner_id == vassal.house_id, Fief.game_id == game_id)
    #                 .order_by(Fief.base_income.desc())
    #                 .limit(1)
    #             )
    #             home_fief = (await self.session.execute(stmt_fief)).scalars().first()

    #             if home_fief:
    #                 # Calculate Stats (Diplomacy etc) - Simplified default for now
    #                 percent = 0.5

    #                 is_combined = "*" if breakdown else ""

    #                 npc_data_list.append(
    #                     {
    #                         "house_id": vassal.house_id,
    #                         "house_name": f"{vassal.name}{is_combined}",  # Add * if combined
    #                         "real_name": vassal.name,
    #                         "max_amount": troops,
    #                         "percent": percent,
    #                         "home_x": home_fief.location_x,
    #                         "home_y": home_fief.location_y,
    #                         "breakdown": breakdown,  # Store for context
    #                     }
    #                 )

    #     return True, npc_data_list, all_player_notifications

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

    # async def prepare_sea_levy_call(
    #     self,
    #     game_id: int,
    #     liege_discord_id: int | None = None,
    #     acting_house_id: int | None = None,
    #     is_gm_override: bool = False,
    # ):
    #     """
    #     Gathers vassal data for a naval levy call, focusing only on vassals
    #     with garrisoned fleets. Now supports GM override.
    #     """
    #     # Determine the effective liege house based on GM override or player's discord_id
    #     liege_house: House | None = None
    #     liege_player: GamePlayer | None = None

    #     if is_gm_override and acting_house_id is not None:
    #         liege_house = await self.session.get(House, acting_house_id)
    #         if not liege_house:
    #             return False, [], []  # Specified NPC house not found
    #         liege_player = GamePlayer(
    #             game_id=game_id,
    #             user_id=0,
    #             claimed_house_id=acting_house_id,
    #             is_primary=True,
    #         )
    #         liege_player.house = liege_house  # Link the house object for access
    #     elif liege_discord_id is not None:
    #         stmt_p = (
    #             select(GamePlayer)
    #             .join(User, GamePlayer.user_id == User.user_id)
    #             .where(
    #                 User.discord_id == liege_discord_id, GamePlayer.game_id == game_id
    #             )
    #             .options(
    #                 selectinload(GamePlayer.house), selectinload(GamePlayer.character)
    #             )
    #         )
    #         liege_player = (await self.session.execute(stmt_p)).scalars().first()
    #         if not liege_player or not liege_player.house:
    #             return False, [], []
    #         liege_house = liege_player.house
    #     else:
    #         return False, [], []  # No way to identify the liege

    #     # Get Liege's stats (same as land version)
    #     diplomacy_stat = 10
    #     if liege_player.character and liege_player.character.skills:
    #         diplomacy_stat = int(liege_player.character.skills.get("diplomacy", 10))
    #     elif is_gm_override and not liege_player.character:
    #         diplomacy_stat = 10

    #     # Find all non-ruined vassals (same as land version)
    #     stmt_v = select(House).where(
    #         House.game_id == game_id,
    #         House.liege_id == liege_house.house_id,
    #         House.is_ruined == False,
    #     )
    #     vassals = (await self.session.execute(stmt_v)).scalars().all()

    #     vassal_data = []
    #     player_vassals = []

    #     # Process each vassal: Separate players and check for fleets
    #     for v in vassals:
    #         # Check if this vassal is a player (same as land version)
    #         stmt_owner = (
    #             select(User)
    #             .join(GamePlayer, User.user_id == GamePlayer.user_id)
    #             .where(
    #                 GamePlayer.game_id == game_id,
    #                 GamePlayer.claimed_house_id == v.house_id,
    #                 GamePlayer.is_primary == True,
    #             )
    #         )
    #         owner_user = (await self.session.execute(stmt_owner)).scalars().first()

    #         # CORE NAVAL LOGIC
    #         # Find their main garrisoned fleet to determine ship count and starting location
    #         stmt_fleet = (
    #             select(Army)
    #             .where(
    #                 Army.house_id == v.house_id,
    #                 Army.army_type == "SEA",
    #                 or_(Army.status == "GARRISONED", Army.status == "IDLE"),
    #             )
    #             .order_by(Army.troop_count.desc())
    #             .limit(1)
    #         )

    #         main_fleet = (await self.session.execute(stmt_fleet)).scalars().first()

    #         # If they are a player vassal, notify them regardless of their fleet status
    #         if owner_user:
    #             player_vassals.append(
    #                 {"house_name": v.name, "discord_id": owner_user.discord_id}
    #             )
    #             continue

    #         # If it's an NPC BUT they have no fleet, they cannot answer the call. Skip them.
    #         if not main_fleet or main_fleet.troop_count == 0:
    #             continue

    #         # Calculate Loyalty and Percentage (same logic, but for ships)
    #         total_ships = main_fleet.troop_count
    #         start_location = (main_fleet.location_x, main_fleet.location_y)

    #         score = 30 + (diplomacy_stat * 2)
    #         # Add other loyalty factors if desired (regional bonus, etc.)

    #         percent = 0.0
    #         if score >= 80:
    #             percent = 0.90
    #         elif score >= 60:
    #             percent = 0.70
    #         elif score >= 40:
    #             percent = 0.30
    #         if total_ships == 0:
    #             percent = 0.0

    #         vassal_data.append(
    #             {
    #                 "house": v,
    #                 "ships": total_ships,
    #                 "percent": percent,
    #                 "score": score,
    #                 "start_location": start_location,
    #                 "source_fleet_id": main_fleet.army_id,
    #             }
    #         )

    #     return True, vassal_data, player_vassals
    async def prepare_sea_levy_call(
        self,
        game_id: int,
        liege_discord_id: int | None = None,
        acting_house_id: int | None = None,
        is_gm_override: bool = False,
    ):
        """
        Gathers vassal data for a naval levy call.
        FIX: Looks for DOCKED fleets and returns consistent home_x/home_y keys.
        """
        # --- 1. DETERMINE LIEGE ---
        liege_house: House | None = None
        liege_player: GamePlayer | None = None

        if is_gm_override and acting_house_id is not None:
            liege_house = await self.session.get(House, acting_house_id)
            if not liege_house:
                return False, [], []
            # Create dummy player wrapper for GM
            liege_player = GamePlayer(
                game_id=game_id,
                user_id=0,
                claimed_house_id=acting_house_id,
                is_primary=True,
            )
            liege_player.house = liege_house
        elif liege_discord_id is not None:
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
            if not liege_player or not liege_player.house:
                return False, [], []
            liege_house = liege_player.house
        else:
            return False, [], []

        # --- 2. CALCULATE STATS ---
        diplomacy_stat = 10
        if liege_player.character and liege_player.character.skills:
            diplomacy_stat = int(liege_player.character.skills.get("diplomacy", 10))
        elif is_gm_override and not liege_player.character:
            diplomacy_stat = 10

        # --- 3. FETCH VASSALS ---
        stmt_v = select(House).where(
            House.game_id == game_id,
            House.liege_id == liege_house.house_id,
            House.is_ruined == False,
        )
        vassals = (await self.session.execute(stmt_v)).scalars().all()

        vassal_data = []
        player_vassals = []

        for v in vassals:
            # Check for Player Owner
            stmt_owner = (
                select(User)
                .join(GamePlayer, User.user_id == GamePlayer.user_id)
                .where(
                    GamePlayer.game_id == game_id,
                    GamePlayer.claimed_house_id == v.house_id,
                    GamePlayer.is_primary == True,
                )
            )
            owner_user = (await self.session.execute(stmt_owner)).scalars().first()

            # Find their main fleet (Must be DOCKED or IDLE)
            stmt_fleet = (
                select(Army)
                .where(
                    Army.house_id == v.house_id,
                    Army.army_type == "SEA",
                    Army.status.in_(
                        ["DOCKED", "IDLE", "GARRISONED"]
                    ),  # Correct statuses for fleets
                )
                .order_by(Army.troop_count.desc())
                .limit(1)
            )
            main_fleet = (await self.session.execute(stmt_fleet)).scalars().first()

            # --- BRANCH A: PLAYER ---
            if owner_user:
                player_vassals.append(
                    {"house_name": v.name, "discord_id": owner_user.discord_id}
                )
                continue

            # --- BRANCH B: NPC ---
            # If NPC has no fleet, they cannot answer. Skip.
            if not main_fleet or main_fleet.troop_count == 0:
                continue

            total_ships = main_fleet.troop_count

            # Use home_x/home_y to match the Land logic and Command expectation
            h_x = main_fleet.location_x
            h_y = main_fleet.location_y

            # Calculate Loyalty Score
            score = 30 + (diplomacy_stat * 2)
            if liege_house.treasury > 5000:
                score += 10

            # Calculate Percentage
            percent = 0.0
            if score >= 80:
                percent = 0.90
            elif score >= 60:
                percent = 0.70
            elif score >= 40:
                percent = 0.30

            if total_ships == 0:
                percent = 0.0

            vassal_data.append(
                {
                    "house": v,  # Kept for potential internal usage, but ID/Name preferred
                    "house_id": v.house_id,
                    "house_name": v.name,
                    "ships": total_ships,
                    "percent": percent,
                    "score": score,
                    "home_x": h_x,  # Consistent Key
                    "home_y": h_y,  # Consistent Key
                    "source_fleet_id": main_fleet.army_id,
                }
            )

        return True, vassal_data, player_vassals

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
