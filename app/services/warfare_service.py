import math
import datetime
import asyncio
from sqlalchemy import select, func, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.repositories import ArmyRepo
from app.db.models import (
    Army,
    GamePlayer,
    Fief,
    User,
    House,
    ArmyContingent,
    Game,
    Battle,
    MarchLog,
)
from app.services.travel_calculator import calculate_travel_duration, format_duration
from app.celery_app import celery_app
import os
from app.tasks.light_tasks import resolve_army_arrival, dispatch_scout_report
import random
from app.services.pathfinder_bot_engine import COSTS
from sqlalchemy.orm.attributes import flag_modified
import copy
from sqlalchemy import update
from app.services.engine_manager import PF_ENGINE
import collections
import io
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from sqlalchemy import select, delete, or_
from app.db.models import PendingInteraction, Army, ArmyContingent, GamePlayer, User
from celery.result import AsyncResult

FOG_OF_WAR_THRESHOLD = 20
FERRY_THRESHOLD = 20  # NEW: Max army size that can use a "ferry"
SEA_FOG_OF_WAR_THRESHOLD = 2


class WarfareService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.cost_map = PF_ENGINE.cost_map  # Add this line
        self.choke_points = []
        for entry in PF_ENGINE.data:
            if entry.get("choke_point") and entry["choke_point"].get("is_active"):
                self.choke_points.append(
                    {
                        "castle": entry["castle"],
                        "house": entry["house"],  # The default owner
                        "x": entry["x"],
                        "y": entry["y"],
                        "connections": entry["choke_point"]["connections"],
                        "desc": entry["choke_point"]["desc"],
                    }
                )

    async def cascade_contribution_update(
        self, game_id: int, call_id: int, liege_id: int, new_percentage: float
    ):
        """
        Recursively updates the contribution percentage for all vassals of a specific house.
        Used when a GM adjusts an NPC Lord Paramount or Major Lord (like Hightower),
        so that their vassals (Beesbury, Cuy) automatically match the levy rate.
        """
        # Lazy import to avoid circular dependencies if models imports service
        from app.db.models import House, BannerContribution

        # 1. Find all direct vassals of the Liege
        # (e.g., Find Beesbury/Cuy where liege_id = Hightower's ID)
        stmt_vassals = select(House.house_id).where(
            House.liege_id == liege_id, House.game_id == game_id
        )
        vassals = await self.session.execute(stmt_vassals)
        vassal_ids = vassals.scalars().all()

        if not vassal_ids:
            return  # Base case: No vassals to update

        # 2. Update the contribution entry for these vassals if they exist for this call
        # We use a bulk update for efficiency
        stmt_update = (
            update(BannerContribution)
            .where(
                BannerContribution.banner_call_id == call_id,
                BannerContribution.house_id.in_(vassal_ids),
            )
            .values(percentage=new_percentage)
        )
        await self.session.execute(stmt_update)

        # 3. Commit the changes for this layer
        await self.session.commit()

        print(
            f"🔄 Cascaded {new_percentage}% contribution to {len(vassal_ids)} vassals of House ID {liege_id}."
        )

        # 4. RECURSION: Do the same for their vassals
        # (e.g., if Cuy has a vassal Knight, update them too)
        for vid in vassal_ids:
            await self.cascade_contribution_update(
                game_id, call_id, vid, new_percentage
            )

    async def resume_march_from_gate(self, army_id: int):
        """
        Called when a gate owner allows an army to pass.
        Triggers a fresh move calculation, which will catch any subsequent gates.
        """
        army = await self.session.get(Army, army_id)
        if not army:
            return False, "❌ Army not found."

        if army.status != "IDLE":
            return False, "❌ Army is not currently halted at a gate."

        # Check if we have a saved destination from the gate alert
        if army.original_destination_x is None or army.original_destination_y is None:
            return (
                False,
                "❌ No previous destination found. Please issue a new move command manually.",
            )

        dest_x = army.original_destination_x
        dest_y = army.original_destination_y

        # Get the name of the final destination for the march_army call
        dest_name = await self.get_location_name_from_coords(
            army.game_id, dest_x, dest_y
        )
        if not dest_name:
            return False, "❌ Could not resolve the original destination's name."

        # --- THE CRITICAL FIX ---
        # Instead of reinventing the logic, we call the main march function again.
        # It will handle pathfinding, splitting ("all" units), and most importantly,
        # it will run _check_gate_interception on the NEW path.
        # The service returns a tuple of 3 items on success
        success, response_data, fog_msg = await self.march_army(
            game_id=army.game_id,
            user_id=None,  # System action
            identifier=str(army.army_id),
            dest_name=dest_name,
            units_input="all",  # Move the whole army
            commander=army.commander_name,
            gold_to_carry=0,  # Gold is already in the army's treasury
            is_gm_override=True,
            acting_house_id=army.house_id,
        )
        # We need to return the same tuple structure
        return success, response_data, fog_msg

    async def _check_gate_interception(
        self, game_id: int, marcher_house_id: int, path_points: list
    ):
        """
        Scans a calculated path to see if it passes through a Choke Point
        controlled by another house.

        Logic:
        1. Checks proximity to defined choke points.
        2. Checks ownership (if marcher owns it, pass).
        3. Checks Whitelist (if owner listed marcher, pass).

        Returns: (GateDict, FiefObj, PathIndex) or (None, None, None)
        """
        INTERCEPTION_RADIUS = (
            15.0  # Pixels. If path gets this close, they are at the gate.
        )

        for i, (px, py) in enumerate(path_points):
            # Skip the first few pixels (so you don't get intercepted by the castle you are leaving)
            if i < 10:
                continue

            for gate in self.choke_points:
                # 1. Check Proximity
                dist = math.sqrt((px - gate["x"]) ** 2 + (py - gate["y"]) ** 2)

                if dist <= INTERCEPTION_RADIUS:
                    # 2. Check Ownership (Dynamic DB check required because owners change)
                    stmt = select(Fief).where(
                        Fief.game_id == game_id, Fief.name == gate["castle"]
                    )
                    gate_fief = (await self.session.execute(stmt)).scalars().first()

                    if not gate_fief:
                        continue  # Should not happen if DB is synced and map config matches DB

                    gate_owner_id = gate_fief.owner_id

                    # 3. Logic: Check if we need to stop the army

                    # A. If marcher owns the gate, they pass freely.
                    if gate_owner_id == marcher_house_id:
                        continue

                    # B. Check Whitelist (Diplomacy)
                    # We need to fetch the owner's House object to see their gate_whitelist settings.
                    gate_owner_house = await self.session.get(House, gate_owner_id)

                    if gate_owner_house and gate_owner_house.gate_whitelist:
                        # gate_whitelist is a JSON list of IDs, e.g., [1, 5, 20]
                        if marcher_house_id in gate_owner_house.gate_whitelist:
                            # They are allowed to pass. Skip interception.
                            continue

                    # C. Interception Triggered
                    # If we reach here, the marcher does NOT own the gate and is NOT whitelisted.
                    return gate, gate_fief, i

        return None, None, None

    async def get_location_name_from_coords(
        self, game_id: int, x: int, y: int
    ) -> str | None:
        """
        Retrieves the name of a fief from its coordinates.
        Returns the name as a string if found, otherwise returns None.
        """
        stmt = select(Fief.name).where(
            Fief.game_id == game_id, Fief.location_x == x, Fief.location_y == y
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _check_command_authority(self, player: GamePlayer, army: Army) -> bool:
        """
        Checks if a player can command an army.
        Allowed if:
        1. Player owns the army directly.
        2. Player is the Liege Lord of the army's House.
        """
        # 1. Direct Ownership
        if army.house_id == player.claimed_house_id:
            return True

        # 2. Liege Lordship (Vassal Command)
        # We need to check if the army's house lists the player's house as liege
        stmt = select(House).where(House.house_id == army.house_id)
        army_house = (await self.session.execute(stmt)).scalars().first()

        if army_house and army_house.liege_id == player.claimed_house_id:
            return True

        return False

    async def _get_gm_settings_from_game(self, game_id: int):
        """Fetches active game rules."""
        game = await self.session.get(Game, game_id)
        if not game:
            return {}, "optimal"

        gm_settings = {
            "twins_open": game.twins_open,
            "rubyford_open": game.rubyford_open,
            "bitterbridge_open": game.bitterbridge_open,
            "rivers_impassable": game.rivers_impassable,
        }
        # If sea travel is disabled globally, force land_only
        effective_mode = "land_only" if not game.sea_travel_allowed else "optimal"
        return gm_settings, effective_mode

    async def _get_location_from_db(self, game_id: int, name: str):
        """
        Smart lookup for location data.
        First, attempts a fast lookup for a Fief by name.
        If no fief is found, it falls back to parsing the input as 'x,y' coordinates.
        """
        # --- Step 1: Attempt to find a Fief by name (the "fast path") ---
        stmt = select(Fief).where(Fief.game_id == game_id, Fief.name.ilike(name))
        fief = (await self.session.execute(stmt)).scalars().first()

        if fief:
            # If we found a matching fief, return its data immediately.
            return {
                "x": fief.location_x,
                "y": fief.location_y,
                "castle": fief.name,
                "region": fief.region,
            }

        # --- Step 2: If no fief was found, try parsing as coordinates ---
        clean_input = name.strip().replace('"', "").replace("'", "")
        if "," in clean_input:
            try:
                x, y = map(int, clean_input.split(","))

                # Validate coordinates against map boundaries
                MAP_WIDTH = 4725
                MAP_HEIGHT = 3601
                if 0 <= x <= MAP_WIDTH and 0 <= y <= MAP_HEIGHT:
                    # Return data in the same dictionary format as a fief
                    return {
                        "x": x,
                        "y": y,
                        "castle": f"Coordinates ({x},{y})",  # Use a descriptive name
                        "region": "The Field",  # Use a sensible default region
                    }
            except ValueError:
                # The input had a comma but wasn't valid integers.
                # We'll let it fall through and return None.
                pass

        # --- Step 3: If it's not a valid fief name and not a valid coordinate pair, it's invalid. ---
        return None

    async def _get_region_from_db(self, game_id: int, x: float, y: float):
        """Fast spatial query for region name."""
        distance = func.sqrt(
            func.pow(Fief.location_x - x, 2) + func.pow(Fief.location_y - y, 2)
        )
        stmt = (
            select(Fief.region)
            .where(Fief.game_id == game_id)
            .order_by(distance.asc())
            .limit(1)
        )
        region = (await self.session.execute(stmt)).scalars().first()
        return region or "the wilderness"

    async def get_fog_of_war_message(
        self, army: Army, game_id: int, start_coords: tuple, direction: str
    ) -> str | None:
        """
        Generates public fog-of-war intel for army movements.
        This version is context-aware and provides different flavor for LAND and SEA armies.
        """

        # --- Region lookup (No changes needed here) ---
        region_name = await self._get_region_from_db(
            game_id, start_coords[0], start_coords[1]
        )
        if not region_name:
            region_name = "an unknown region"

        # --- ETA Calculation (No changes needed here) ---
        now = datetime.datetime.now(datetime.timezone.utc)
        if not army.arrival_time:
            eta = "??:??"
        else:
            remaining = max((army.arrival_time - now).total_seconds(), 0)
            hours, minutes = int(remaining // 3600), int((remaining % 3600) // 60)
            eta = f"{hours:02d}:{minutes:02d}"

        # --- NEW: Type-Specific Flavor Text ---
        styles = []
        approx_unit_desc = ""

        # --- Case 1: It's a Fleet ---
        if army.army_type == "SEA":
            approx_unit_desc = (
                f"{army.troop_count} ships"  # Use "ships" instead of "men"
            )

            styles = [
                # 1. Naval Dispatch
                f"Naval Report: {approx_unit_desc} spotted sailing {direction} from the coast of @{region_name}. (ETA {eta})",
                # 2. Scout/Merchant Report
                f"Sails sighted! A fleet of {approx_unit_desc} is navigating the waters near @{region_name}. (ETA {eta})",
                # 3. Official Movement Report
                f"Movement Report: {approx_unit_desc} making way {direction} off @{region_name}. (ETA {eta})",
                # 4. General Intel
                f"Intel: A naval force of {approx_unit_desc} is underway near @{region_name}. (ETA {eta})",
                # 5. More Flavor
                f"Unidentified banners on {approx_unit_desc} seen off the coast of @{region_name}, heading {direction}. (ETA {eta})",
                f"{approx_unit_desc} are on the move near @{region_name}. (ETA {eta})",
            ]

        # --- Case 2: It's a Land Army (your original styles) ---
        else:
            approx_unit_desc = f"~{army.troop_count} men"

            styles = [
                f"{approx_unit_desc} observed moving {direction} through @{region_name}. (ETA {eta})",
                f"Movement Report: {approx_unit_desc} advancing {direction} near @{region_name}. (ETA {eta})",
                f"{approx_unit_desc} marching {direction} across @{region_name} (ETA {eta})",
                f"Update: Approximately {approx_unit_desc} detected moving {direction} in @{region_name}. (ETA {eta})",
                f"Intel: Forces numbering {approx_unit_desc} are shifting {direction} near @{region_name}. (ETA {eta})",
                f"Scouts note {approx_unit_desc} heading {direction} past @{region_name}. (ETA {eta})",
            ]

        return f"{random.choice(styles)}"

    def calculate_direction(self, start: tuple, end: tuple) -> str:
        x1, y1 = start
        x2, y2 = end

        dx = x2 - x1
        dy = y2 - y1

        if dx == 0 and dy == 0:
            return "arrived"

        # Convert to compass angle (0° = North, clockwise)
        angle = math.degrees(math.atan2(dx, -dy))
        angle = (angle + 360) % 360

        # 16-point compass abbreviations
        abbrev = [
            "N",
            "NNE",
            "NE",
            "ENE",
            "E",
            "ESE",
            "SE",
            "SSE",
            "S",
            "SSW",
            "SW",
            "WSW",
            "W",
            "WNW",
            "NW",
            "NNW",
        ]

        # Full names for each
        full = {
            "N": "North",
            "NNE": "North-Northeast",
            "NE": "Northeast",
            "ENE": "East-Northeast",
            "E": "East",
            "ESE": "East-Southeast",
            "SE": "Southeast",
            "SSE": "South-Southeast",
            "S": "South",
            "SSW": "South-Southwest",
            "SW": "Southwest",
            "WSW": "West-Southwest",
            "W": "West",
            "WNW": "West-Northwest",
            "NW": "Northwest",
            "NNW": "North-Northwest",
        }

        sector = 360 / 16
        index = int((angle + sector / 2) // sector) % 16
        direction_abbrev = abbrev[index]

        return full[direction_abbrev]

    async def plan_journey(
        self,
        game_id: int,
        source_army_id: int,
        dest_name: str,
        units_input: str,
        travel_mode_req: str,
        waypoints: str | None = None,
    ):
        """
        Calculates a path and travel time without creating or moving any armies.
        This is for planning purposes only.
        """
        # 1. Setup & Initial Validation
        gm_settings, _ = await self._get_gm_settings_from_game(game_id)

        # The 'final_travel_mode' is now correctly set to the user's requested mode.
        # We no longer hardcode it to "land_only".
        final_travel_mode = travel_mode_req

        source_army = await ArmyRepo.get_army_by_id(self.session, source_army_id)
        if not source_army:
            return False, "❌ Source army for planning not found."

        # 2. Pathfinding & Waypoint Preparation
        origin_name = await self.get_location_name_from_coords(
            game_id, source_army.location_x, source_army.location_y
        )
        dest_coords = await self._get_location_from_db(game_id, dest_name)

        if not dest_coords:
            return False, f"❌ Destination '{dest_name}' is invalid or does not exist."
        if dest_coords["x"] == 0 and dest_coords["y"] == 0:
            return (
                False,
                f"❌ The location '{dest_name}' has no defined position on the map. Please use pixel coordinates (e.g., `1234,5678`) for this destination instead.",
            )
        if origin_name and origin_name.lower() == dest_name.lower():
            return False, "❌ Destination cannot be the same as the start location."

        parsed_waypoints = (
            [wp.strip() for wp in waypoints.split(";")] if waypoints else []
        )
        start_coords = (source_army.location_x, source_army.location_y)

        # The pathfinder now receives the correct travel mode
        path_data = await PF_ENGINE.find_journey_async(
            start_loc=start_coords,
            end_loc=(dest_coords["x"], dest_coords["y"]),
            waypoints=parsed_waypoints,
            travel_mode=final_travel_mode,
            gm_settings=gm_settings,
        )
        if not path_data:
            return (
                False,
                "❌ No path could be calculated. Check your waypoints and destination.",
            )

        # 3. Calculate Army Size for Simulation
        try:
            army_size = 0
            clean_units_input = units_input.strip().lower()
            # Corrected: Need to account for the unit type of the source army for "all"
            unit_count = source_army.troop_count

            if clean_units_input == "all" or (
                clean_units_input.isdigit() and int(clean_units_input) >= unit_count
            ):
                army_size = unit_count
            elif clean_units_input.isdigit():
                army_size = int(clean_units_input)
            else:
                total_moving = 0
                parts = clean_units_input.replace(",", " ").split()
                for p in parts:
                    if ":" in p:
                        try:
                            _, count_str = p.split(":", 1)
                            total_moving += int(count_str)
                        except (ValueError, IndexError):
                            continue
                army_size = total_moving

            if army_size <= 0:
                raise ValueError("Invalid unit format or zero troops specified.")

            duration = calculate_travel_duration(
                path_data["terrain_breakdown"], army_size
            )
        except ValueError as e:
            return False, f"❌ Error processing units: {e}", None

        # 4. Return Results (NO database changes or tasks)
        return (
            True,
            {
                "image": path_data["image"],
                "time": format_duration(duration),
                "distance": int(path_data["total_distance"]),
                "origin": origin_name or "The field",
                "destination": dest_name,
                "mode": final_travel_mode,  # This now correctly reflects the chosen mode
                "army_size": army_size,
            },
        )

    def _generate_path_image(
        self, path_points: list, all_stops_coords: list
    ) -> io.BytesIO | None:
        """
        Generates the visual path image from path_points.
        This is a synchronous method to be called via asyncio.to_thread.
        """
        try:
            # Create figure
            fig = Figure(figsize=(10, 7.5))
            ax = fig.add_subplot(111)
            ax.axis("off")

            # Draw the base map from the Global Engine
            if hasattr(PF_ENGINE, "map_visual_rgb"):
                ax.imshow(PF_ENGINE.map_visual_rgb)

            # --- DRAW PATH LINES ---
            path_segments = []
            current_segment, current_terrain = [], None

            # Use PF_ENGINE.cost_map directly
            rows, cols = PF_ENGINE.cost_map.shape

            for i in range(len(path_points)):
                x, y = int(path_points[i][0]), int(path_points[i][1])

                # Bounds check
                if not (0 <= y < rows and 0 <= x < cols):
                    continue

                code = PF_ENGINE.cost_map[y, x]
                terrain = (
                    "sea"
                    if code in [COSTS["ocean"], COSTS["coastal_water"], COSTS["port"]]
                    else "land"
                )

                if current_terrain is None:
                    current_terrain = terrain

                if terrain != current_terrain:
                    current_segment.append((x, y))
                    path_segments.append({"t": current_terrain, "p": current_segment})
                    current_segment = [(x, y)]
                    current_terrain = terrain
                else:
                    current_segment.append((x, y))

            if current_segment:
                path_segments.append({"t": current_terrain, "p": current_segment})

            # Plot segments
            for seg in path_segments:
                if len(seg["p"]) < 2:
                    continue
                px, py = zip(*seg["p"])
                color = "blue" if seg["t"] == "sea" else "red"
                ax.plot(px, py, color=color, linewidth=2.5, alpha=0.8)

            # --- DRAW STOPS ---
            if all_stops_coords:
                # 1. Start (Green)
                sx, sy = all_stops_coords[0]
                ax.plot(
                    sx, sy, "go", markersize=10, markeredgecolor="black", label="Start"
                )

                # 2. End (Purple)
                ex, ey = all_stops_coords[-1]
                ax.plot(
                    ex,
                    ey,
                    "o",
                    color="purple",
                    markersize=10,
                    markeredgecolor="black",
                    label="Dest",
                )

                # 3. Waypoints/Landings (Yellow)
                for stop in all_stops_coords[1:-1]:
                    # Handle both dict format and tuple format just in case
                    if isinstance(stop, dict):
                        wx, wy = stop["x"], stop["y"]
                    else:
                        wx, wy = stop[0], stop[1]
                    ax.plot(wx, wy, "yo", markersize=9, markeredgecolor="black")

            # Save to buffer
            image_buffer = io.BytesIO()
            canvas = FigureCanvasAgg(fig)
            canvas.print_png(image_buffer)
            image_buffer.seek(0)

            del fig, canvas, ax
            return image_buffer

        except Exception as e:
            print(f"❌ Failed to generate path image: {e}")
            import traceback

            traceback.print_exc()
            return None

    async def halt_army_at_intercept(
        self, army_id: int, intercept_x: float, intercept_y: float
    ):
        """
        Stops a moving army at a specific future intercept point.
        This is used by the interaction system.
        """
        army = await ArmyRepo.get_army_by_id(self.session, army_id)
        if not army or army.status not in ["MARCHING", "SAILING"]:
            return False, "Army not found or not moving."

        # Revoke the original arrival task
        if army.task_id:
            from celery.result import AsyncResult

            AsyncResult(army.task_id).revoke(terminate=True)

        # Force the army's location to the intercept point
        army.location_x = intercept_x
        army.location_y = intercept_y

        # Reset movement status and clear destination
        army.status = "IDLE"
        army.destination_x, army.destination_y = None, None
        army.arrival_time, army.departure_time, army.task_id = None, None, None

        # CLEANUP MARCH LOGS
        await ArmyRepo.clear_march_logs(self.session, army_id)

        await self.session.commit()

        region = await self._get_region_from_db(
            army.game_id, army.location_x, army.location_y
        )
        return True, f"Army {army.commander_name} has been halted in {region}."

    async def march_army(
        self,
        game_id: int,
        user_id: int,
        identifier: str,
        dest_name: str,
        units_input: str,
        commander: str | None,
        gold_to_carry: int = 0,
        waypoints: str | None = None,
        travel_mode_req: str = "land_only",
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ):
        """
        Handles marching logic. This version includes:
        - Reactive Gate Alerts for NPC choke points.
        - The trigger for the new Player-vs-Player Interaction system.
        - GM override capability.
        """
        # 1. Setup & Initial Validation
        gm_settings, _ = await self._get_gm_settings_from_game(game_id)

        source_army = await ArmyRepo.get_army_by_id(self.session, int(identifier))
        if not source_army:
            return False, f"❌ Army ID {identifier} not found.", None

        player: GamePlayer | None = None
        player_claimed_house_id: int | None = None
        if not is_gm_override:
            player = await self.session.scalar(
                select(GamePlayer)
                .where(GamePlayer.user_id == user_id, GamePlayer.game_id == game_id)
                .options(selectinload(GamePlayer.house))
            )
            if not player or not player.claimed_house_id:
                return False, "❌ You do not command a house.", None
            player_claimed_house_id = player.claimed_house_id

        effective_commanding_house_id: int | None = None
        if is_gm_override:
            if acting_house_id is not None:
                effective_commanding_house_id = acting_house_id
            else:
                effective_commanding_house_id = source_army.house_id
        else:
            effective_commanding_house_id = player_claimed_house_id

        if effective_commanding_house_id is None:
            return (
                False,
                "❌ Cannot determine the commanding house for this action.",
                None,
            )

        if source_army.army_type == "SEA":
            return (
                False,
                "❌ Fleets must use the `!sail` command.",
                None,
            )
        if not is_gm_override and not await self._check_command_authority(
            player, source_army
        ):
            return (
                False,
                f"❌ You do not have authority over {source_army.commander_name}.",
                None,
            )
        # If GM is overriding, also ensure the army belongs to the house the GM specified (or the army's current house).
        if is_gm_override and source_army.house_id != effective_commanding_house_id:
            return (
                False,
                f"❌ GM override: Army {source_army.commander_name} does not belong to the specified acting house ID {effective_commanding_house_id}.",
                None,
            )

        if source_army.status == "MARCHING":
            return (
                False,
                "❌ This army is already marching. Use `!redirect`.",
                None,
            )

        # 2. Pathfinding
        origin_name = await self.get_location_name_from_coords(
            game_id, source_army.location_x, source_army.location_y
        )
        dest_coords = await self._get_location_from_db(game_id, dest_name)

        if not dest_coords or (dest_coords["x"] == 0 and dest_coords["y"] == 0):
            return False, f"❌ Destination '{dest_name}' is invalid.", None
        if origin_name and origin_name.lower() == dest_name.strip().lower():
            return False, "❌ Cannot march to your current location.", None

        parsed_waypoints = (
            [wp.strip() for wp in waypoints.split(";")] if waypoints else []
        )
        start_coords = (int(source_army.location_x), int(source_army.location_y))

        # Use the requested travel mode, allowing banner calls to override the default
        path_data = await PF_ENGINE.find_journey_async(
            start_loc=start_coords,
            end_loc=(dest_coords["x"], dest_coords["y"]),
            waypoints=parsed_waypoints,
            travel_mode=travel_mode_req,
            gm_settings=gm_settings,
        )
        # Fallback to 'optimal' if the requested mode fails (e.g., player trying land_only to an island)
        if not path_data and travel_mode_req == "land_only":
            path_data = await PF_ENGINE.find_journey_async(
                start_loc=start_coords,
                end_loc=(dest_coords["x"], dest_coords["y"]),
                waypoints=parsed_waypoints,
                travel_mode="optimal",
                gm_settings=gm_settings,
            )
        if not path_data:
            return False, "❌ No viable path could be calculated.", None

        # 3. Determine Army to Move & Calculate Duration
        new_commander_name = commander or source_army.commander_name
        if not commander and "Garrison of" in source_army.commander_name:
            if is_gm_override and acting_house_id:
                acting_house = await self.session.get(House, acting_house_id)
                if acting_house:
                    new_commander_name = f"Captain of {acting_house.name}"
            elif player and player.house:
                new_commander_name = f"Captain of {player.house.name}"
            else:
                new_commander_name = (
                    "Army Commander"  # Fallback if no specific house name is found
                )

        try:
            # This logic correctly determines the army to move (either the full stack or a new split-off army)
            clean_units_input = units_input.strip().lower()
            if clean_units_input == "all" or (
                clean_units_input.isdigit()
                and int(clean_units_input) >= source_army.troop_count
            ):
                duration = calculate_travel_duration(
                    path_data["terrain_breakdown"], source_army.troop_count
                )
                army_to_move = source_army
            elif clean_units_input.isdigit():
                amount = int(clean_units_input)
                duration = calculate_travel_duration(
                    path_data["terrain_breakdown"], amount
                )
                army_to_move = await ArmyRepo.create_marching_army(
                    self.session, source_army, amount, new_commander_name
                )
            else:
                specific_comp, total_moving = {}, 0
                normalized_input = clean_units_input.replace(",", " ")
                parts = normalized_input.split()
                for p in parts:
                    if ":" in p:
                        try:
                            unit, count_str = p.split(":", 1)
                            count = int(count_str)
                            valid_units = ["infantry", "cavalry", "archers"]
                            matched_unit = next(
                                (u for u in valid_units if u.startswith(unit)), None
                            )
                            if matched_unit:
                                specific_comp[matched_unit] = count
                                total_moving += count
                        except (ValueError, IndexError):
                            continue
                if total_moving <= 0:
                    return (
                        False,
                        "❌ Invalid unit format. Example: `inf:500 cav:200`",
                        None,
                    )
                duration = calculate_travel_duration(
                    path_data["terrain_breakdown"], total_moving
                )
                army_to_move = await ArmyRepo.create_marching_army_specific(
                    self.session, source_army, specific_comp, new_commander_name
                )
        except ValueError as e:
            return False, f"❌ Error processing units: {e}", None

        SEA_TOLERANCE = 25.0

        sea_dist = path_data["terrain_breakdown"].get("sea", 0.0)

        # Calculate ratio: If > 50% of the trip is water, it's not a march.
        total_dist = path_data.get("total_distance", 1.0)

        if sea_dist > SEA_TOLERANCE and army_to_move.troop_count >= FERRY_THRESHOLD:
            return (
                False,
                f"❌ **Water Crossing Too Wide:** The route requires crossing {int(sea_dist)} distance of water.\n"
                f"For an army of **{army_to_move.troop_count}** men, you must use a Fleet (`!sail`).\n"
                f"*(Ferry Limit: <{int(SEA_TOLERANCE)} distance)*",
                None,
            )
        # 5. DB Update & Task Scheduling
        now = datetime.datetime.now(datetime.timezone.utc)
        arrival_time = now + datetime.timedelta(seconds=duration)
        house_for_transaction = await self.session.get(
            House, effective_commanding_house_id
        )
        if not house_for_transaction:
            return False, "❌ Commanding house not found for gold transaction.", None

        if gold_to_carry > house_for_transaction.treasury:
            return (
                False,
                f"❌ Not enough gold! House {house_for_transaction.name} has {house_for_transaction.treasury}, tried to take {gold_to_carry}.",
                None,
            )

        house_for_transaction.treasury -= gold_to_carry
        army_to_move.treasury = (army_to_move.treasury or 0) + gold_to_carry

        # Store both current and original destination
        army_to_move.destination_x = dest_coords["x"]
        army_to_move.destination_y = dest_coords["y"]
        army_to_move.original_destination_x = dest_coords["x"]
        army_to_move.original_destination_y = dest_coords["y"]

        army_to_move.departure_time = now
        army_to_move.arrival_time = arrival_time
        army_to_move.status = "MARCHING"
        army_to_move.commander_name = new_commander_name

        # Schedule the final arrival task
        task = resolve_army_arrival.apply_async(
            args=[army_to_move.army_id], eta=arrival_time
        )
        army_to_move.task_id = task.id
        await self.session.commit()  # First commit to save the army state

        # 6. Logging & Interception Scheduling
        path_points = path_data.get("path_points", [])
        await ArmyRepo.log_march_path(
            self.session, army_to_move.army_id, game_id, path_points, now, duration
        )

        # --- A. Reactive Gate Alerts (for NPC Choke Points) ---
        gate_info, gate_fief, cut_index = await self._check_gate_interception(
            game_id, effective_commanding_house_id, path_points
        )
        if gate_info:
            ratio_to_gate = (cut_index + 1) / len(path_points) if path_points else 1
            duration_to_gate = duration * ratio_to_gate
            gate_arrival_time = now + datetime.timedelta(seconds=duration_to_gate)

            from app.tasks.light_tasks import dispatch_gate_alert

            dispatch_gate_alert.apply_async(
                args=[
                    game_id,
                    army_to_move.army_id,
                    gate_info["castle"],
                    gate_fief.owner_id,
                ],
                eta=gate_arrival_time,
            )

        # --- B. Player Interaction System Trigger ---
        # Local import to prevent circular dependency
        from app.tasks.light_tasks import initiate_player_interaction

        collisions = await self.check_interceptions_advanced(
            game_id, army_to_move.army_id, path_points, now, duration
        )

        # 1. Filter for valid LAND enemies only
        unique_enemy_ids = {c["enemy_id"] for c in collisions}
        valid_land_ids = set()

        if unique_enemy_ids:
            stmt_types = select(Army.army_id).where(
                Army.army_id.in_(unique_enemy_ids), Army.army_type == "LAND"
            )
            valid_land_ids = set(
                (await self.session.execute(stmt_types)).scalars().all()
            )

        # 2. Create a clean list of valid collisions
        #    (Must be Land type, and not a duplicate check of the same enemy)
        valid_collisions = []
        seen_ids = set()

        # Sort collisions by TIME (Earliest first)
        # check_interceptions_advanced usually returns them in order, but we ensure it here.
        collisions.sort(key=lambda x: x["time"])

        for col in collisions:
            e_id = col["enemy_id"]
            if e_id in valid_land_ids and e_id not in seen_ids:
                valid_collisions.append(col)
                seen_ids.add(e_id)

        # 3. PICK THE FIRST ONE ONLY
        if valid_collisions:
            first_contact = valid_collisions[0]

            # Schedule
            prompt_time = first_contact["time"] - datetime.timedelta(hours=1)
            if prompt_time < now:
                prompt_time = now + datetime.timedelta(seconds=15)

            print(
                f"[DEBUG MARCH] Scheduled FIRST Land Interception with Army {first_contact['enemy_id']} at {first_contact['time']}"
            )

            initiate_player_interaction.apply_async(
                args=[
                    game_id,
                    army_to_move.army_id,
                    first_contact["enemy_id"],
                    first_contact["time"],
                    first_contact["coords"][0],
                    first_contact["coords"][1],
                ],
                eta=prompt_time,
            )

        await self.session.commit()  # Second commit to save any new task IDs if needed

        # 7. Prepare and Return Final Response
        direction = self.calculate_direction(
            start_coords, (dest_coords["x"], dest_coords["y"])
        )
        fog_msg = await self.get_fog_of_war_message(
            army_to_move, game_id, start_coords, direction
        )
        if army_to_move.troop_count < FOG_OF_WAR_THRESHOLD:
            fog_msg = None

        response_data = {
            "image": path_data["image"],
            "time": format_duration(duration),
            "distance": int(path_data["total_distance"]),
            "commander": army_to_move.commander_name,
            "count": army_to_move.troop_count,
            "origin": origin_name or "The field",
            "destination": dest_name,
            "army_id": army_to_move.army_id,
            "gold_carried": gold_to_carry,
        }

        return True, response_data, fog_msg

    async def stop_march(
        self,
        game_id: int,
        user_id: int,
        army_id: int,
        is_admin: bool = False,
        is_gm_override: bool = False,
    ):
        """
        Stops a moving army or fleet immediately.
        Calculates exact position based on elapsed time and clears any pending land legs.
        """
        from sqlalchemy.orm.attributes import flag_modified  # Required for JSON updates

        army = await ArmyRepo.get_army_by_id(self.session, army_id)
        if not army:
            return False, "❌ Army not found."

        # 1. Authority Check
        if not is_admin and not is_gm_override:
            stmt_p = select(GamePlayer).where(
                GamePlayer.user_id == user_id, GamePlayer.game_id == game_id
            )
            player = (await self.session.execute(stmt_p)).scalars().first()

            if not player or not await self._check_command_authority(player, army):
                return False, "❌ You do not have authority to halt this host."

        # 2. Status Validation
        if army.status not in ["MARCHING", "SAILING"]:
            return False, "⚠️ This unit is not currently in motion."

        # 3. Task Revocation
        if army.task_id:
            try:
                celery_app.control.revoke(army.task_id, terminate=True)
            except Exception as e:
                print(f"[WARFARE] Task revocation failed for {army_id}: {e}")

        # 4. Interpolate Position (Stop exactly where they are now)
        now = datetime.datetime.now(datetime.timezone.utc)
        if army.arrival_time and army.departure_time:
            # Ensure we are using UTC-aware timestamps for math
            dep_time = (
                army.departure_time.replace(tzinfo=datetime.timezone.utc)
                if army.departure_time.tzinfo is None
                else army.departure_time
            )
            arr_time = (
                army.arrival_time.replace(tzinfo=datetime.timezone.utc)
                if army.arrival_time.tzinfo is None
                else army.arrival_time
            )

            total_dur = (arr_time - dep_time).total_seconds()
            elapsed = (now - dep_time).total_seconds()
            progress = min(1.0, max(0.0, elapsed / total_dur if total_dur > 0 else 1.0))
        else:
            progress = 0.0

        # Update coords based on progress percentage
        army.location_x = (
            army.location_x + (army.destination_x - army.location_x) * progress
        )
        army.location_y = (
            army.location_y + (army.destination_y - army.location_y) * progress
        )

        # 5. Handle Hybrid Journey Cleanup (The 'Better Method' Fix)
        # If this is a fleet with a pending land march, we must delete the march data.
        if army.army_type == "SEA" and army.cargo and "pending_march" in army.cargo:
            del army.cargo["pending_march"]
            flag_modified(army, "cargo")
            print(
                f"[DEBUG] Cleared pending land leg for Fleet {army.army_id} during halt."
            )

        # 6. Clear Movement Fields
        army.status = "IDLE"
        army.destination_x = None
        army.destination_y = None
        army.arrival_time = None
        army.departure_time = None
        army.task_id = None

        # 7. Cleanup trajectory logs used for interceptions
        await ArmyRepo.clear_march_logs(self.session, army_id)

        await self.session.commit()

        # 8. Resolve Final Location Flavor Text
        region = await self._get_region_from_db(
            game_id, army.location_x, army.location_y
        )
        unit_type = "fleet" if army.army_type == "SEA" else "army"

        return (
            True,
            f"🛑 **Halt!** The {unit_type} **{army.commander_name}** has stopped its movement in **{region}**.",
        )

    async def set_world_rule(self, game_id: int, rule_name: str, value: bool):
        """Sets a boolean world rule."""
        valid_rules = [
            "twins_open",
            "rubyford_open",
            "bitterbridge_open",
            "rivers_impassable",
            "sea_travel_allowed",
        ]
        if rule_name not in valid_rules:
            return f"❌ Invalid rule '{rule_name}'."

        stmt = update(Game).where(Game.game_id == game_id).values({rule_name: value})
        await self.session.execute(stmt)
        await self.session.commit()
        status = "ENABLED" if value else "DISABLED"
        return f"✅ World rule **{rule_name}** has been set to **{status}**."

    async def split_army(
        self,
        game_id: int,
        user_id: int,
        army_id: int,
        split_amount: int,
        new_name: str,
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ):
        """
        Splits a portion of an army or fleet into a new unit.
        Correctly validates command authority for lieges and owners.
        """
        army = await ArmyRepo.get_army_by_id(self.session, army_id)
        if not army:
            return False, "❌ Army not found."

        player: GamePlayer | None = None
        player_claimed_house_id: int | None = None

        if not is_gm_override:
            player = await self.session.scalar(
                select(GamePlayer).where(
                    GamePlayer.user_id == user_id, GamePlayer.game_id == game_id
                )
            )
            if not player or not player.claimed_house_id:
                return False, "❌ You do not command a house."
            player_claimed_house_id = player.claimed_house_id

        effective_commanding_house_id: int | None = None
        if is_gm_override:
            if acting_house_id is not None:
                effective_commanding_house_id = acting_house_id
            else:
                effective_commanding_house_id = army.house_id
        else:
            effective_commanding_house_id = player_claimed_house_id

        if effective_commanding_house_id is None:
            return False, "❌ Cannot determine the commanding house for this action."

        # Authority Check
        if not is_gm_override and not await self._check_command_authority(player, army):
            return (
                False,
                f"❌ You do not have command authority over **{army.commander_name}**.",
            )

        if is_gm_override and army.house_id != effective_commanding_house_id:
            return (
                False,
                f"❌ GM override: Army {army.commander_name} does not belong to the specified acting house ID {effective_commanding_house_id}.",
            )

        # Validation
        if split_amount <= 0 or split_amount >= army.troop_count:
            return (
                False,
                "❌ Split amount must be a positive number and less than the army's total size.",
            )

        if army.status in ["MARCHING", "SAILING"]:
            return (
                False,
                "❌ Cannot split a moving unit. Use the `!stop` command first.",
            )

        # The repository handles the logic of creating the new army and adjusting the old one.
        new_army = await ArmyRepo.split_army_logic(
            self.session, army, split_amount, new_name
        )
        await self.session.commit()

        unit_label = "ships" if new_army.army_type == "SEA" else "men"
        cargo_msg = ""
        if new_army.cargo and new_army.cargo.get("troop_count", 0) > 0:
            cargo_msg = f" (carrying {new_army.cargo['troop_count']} men)"

        return (
            True,
            f"✅ Unit split. Created **{new_name}** with **{new_army.troop_count} {unit_label}**{cargo_msg}. The original unit now has **{army.troop_count} {unit_label}**.",
        )

    async def form_coalition(
        self,
        game_id: int,
        leader_user_id: int,
        new_name: str,
        army_ids: tuple,
        bypass_auth: bool = False,
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ):
        """
        Merges multiple armies or fleets into a single new coalition army.
        Supports GM overrides to merge armies from different houses.
        """
        if len(army_ids) < 2:
            return False, "❌ You must select at least two units to form a coalition."

        # Fetch all armies at once
        armies_to_merge = await ArmyRepo.get_armies_by_ids(
            self.session, list(set(army_ids))
        )
        if len(armies_to_merge) != len(set(army_ids)):
            return False, "❌ One or more army IDs are invalid."

        # Determine the Player (if not GM override)
        player: GamePlayer | None = None
        if not is_gm_override:
            stmt = (
                select(GamePlayer)
                .join(User, User.user_id == GamePlayer.user_id)
                .where(
                    User.discord_id == leader_user_id,
                    GamePlayer.game_id == game_id,
                )
            )
            player = (await self.session.execute(stmt)).scalars().first()
            if not player:
                return False, "❌ System Error: Player not found."

        # Determine the "Effective Commander" (Who owns the resulting coalition?)
        effective_commanding_house_id: int | None = None

        if is_gm_override:
            if acting_house_id is None:
                return (
                    False,
                    "❌ GM override requires an acting house ID (Target House).",
                )
            effective_commanding_house_id = acting_house_id
        else:
            if not player.claimed_house_id:
                return False, "❌ You do not command a house."
            effective_commanding_house_id = player.claimed_house_id

        # Rigorous Validation Loop
        first_army = armies_to_merge[0]
        first_army_type = first_army.army_type
        ref_x, ref_y = first_army.location_x, first_army.location_y

        for army in armies_to_merge:
            # 1. Check Authority
            if not is_gm_override and not bypass_auth:
                # Normal Player Check: Must own the army or be its liege
                if player is None or not await self._check_command_authority(
                    player, army
                ):
                    return (
                        False,
                        f"❌ You do not have command authority over **{army.commander_name}**.",
                    )

            # GM Override: We intentionally SKIP the check ensuring army.house_id == acting_house_id.
            # This allows the GM to merge armies from DIFFERENT houses.

            # 2. Check State
            if army.is_coalition:
                return (
                    False,
                    f"❌ **{army.commander_name}** is already a coalition. Disband it first.",
                )

            if army.status in ["MARCHING", "SAILING"]:
                return False, f"❌ **{army.commander_name}** is currently moving."

            if army.army_type != first_army_type:
                return False, "❌ You cannot merge land armies with fleets."

            # 3. Check Distance (15px Tolerance)
            dist = math.sqrt(
                (army.location_x - ref_x) ** 2 + (army.location_y - ref_y) ** 2
            )
            if dist > 15.0:
                return (
                    False,
                    f"❌ **{army.commander_name}** is too far away ({dist:.1f} px).",
                )

        # Create Coalition Shell
        coalition = Army(
            game_id=game_id,
            house_id=effective_commanding_house_id,  # Owned by the Leader House
            commander_name=new_name,
            is_coalition=True,
            army_type=first_army_type,
            troop_count=0,
            composition={},
            location_x=ref_x,
            location_y=ref_y,
            status="IDLE",
            treasury=0,
        )
        self.session.add(coalition)
        await self.session.flush()  # Generate ID

        # Calculate Totals & Create Contingents
        contingents = []
        total_comp = {}
        total_cargo_comp = {}
        total_troops = 0
        total_cargo_troops = 0
        total_gold = 0

        for a in armies_to_merge:
            # Create Contingent
            # CRITICAL: We use a.house_id as 'original_house_id'.
            # This preserves ownership even if the coalition leader is different.
            contingents.append(
                ArmyContingent(
                    parent_army_id=coalition.army_id,
                    original_house_id=a.house_id,
                    troop_count=a.troop_count,
                    composition=a.composition,
                    cargo=a.cargo,
                    treasury=a.treasury or 0,
                )
            )

            # Sum stats
            total_troops += a.troop_count
            total_gold += a.treasury or 0

            for unit, count in a.composition.items():
                total_comp[unit] = total_comp.get(unit, 0) + count

            if a.cargo:
                total_cargo_troops += a.cargo.get("troop_count", 0)
                for unit, count in a.cargo.get("composition", {}).items():
                    total_cargo_comp[unit] = total_cargo_comp.get(unit, 0) + count

        # Finalize the Coalition Army
        coalition.composition = total_comp
        coalition.troop_count = total_troops
        coalition.treasury = total_gold

        if total_cargo_troops > 0:
            coalition.cargo = {
                "commander": f"Embarked forces of {new_name}",
                "troop_count": total_cargo_troops,
                "composition": total_cargo_comp,
            }

        self.session.add_all(contingents)

        # Delete old armies
        for a in armies_to_merge:
            await self.session.delete(a)

        await self.session.commit()

        unit_label = "ships" if first_army_type == "SEA" else "men"
        cargo_label = (
            f" and carrying **{total_cargo_troops} men**"
            if total_cargo_troops > 0
            else ""
        )

        return (
            True,
            f"🤝 **Coalition Formed!** The **{new_name}** has been created with **{total_troops} {unit_label}**{cargo_label}.",
        )

    async def form_coalition(
        self,
        game_id: int,
        leader_user_id: int,
        new_name: str,
        army_ids: tuple,
        bypass_auth: bool = False,
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ):
        """
        Merges multiple armies or fleets into a single new coalition army.
        Supports GM overrides to merge armies from different houses.
        """
        if len(army_ids) < 2:
            return False, "❌ You must select at least two units to form a coalition."

        # Fetch all armies at once
        armies_to_merge = await ArmyRepo.get_armies_by_ids(
            self.session, list(set(army_ids))
        )
        if len(armies_to_merge) != len(set(army_ids)):
            return False, "❌ One or more army IDs are invalid."

        # Determine the Player (if not GM override)
        player: GamePlayer | None = None
        if not is_gm_override:
            stmt = (
                select(GamePlayer)
                .join(User, User.user_id == GamePlayer.user_id)
                .where(
                    User.discord_id == leader_user_id,
                    GamePlayer.game_id == game_id,
                )
            )
            player = (await self.session.execute(stmt)).scalars().first()
            if not player:
                return False, "❌ System Error: Player not found."

        # Determine the "Effective Commander" (Who owns the resulting coalition?)
        effective_commanding_house_id: int | None = None

        if is_gm_override:
            if acting_house_id is None:
                return (
                    False,
                    "❌ GM override requires an acting house ID (Target House).",
                )
            effective_commanding_house_id = acting_house_id
        else:
            if not player.claimed_house_id:
                return False, "❌ You do not command a house."
            effective_commanding_house_id = player.claimed_house_id

        # Rigorous Validation Loop
        first_army = armies_to_merge[0]
        first_army_type = first_army.army_type
        ref_x, ref_y = first_army.location_x, first_army.location_y

        for army in armies_to_merge:
            # 1. Check Authority
            if not is_gm_override and not bypass_auth:
                if player is None or not await self._check_command_authority(
                    player, army
                ):
                    return (
                        False,
                        f"❌ You do not have command authority over **{army.commander_name}**.",
                    )

            # 2. Check State
            if army.is_coalition:
                return (
                    False,
                    f"❌ **{army.commander_name}** is already a coalition. Disband it first.",
                )

            if army.status in ["MARCHING", "SAILING"]:
                return False, f"❌ **{army.commander_name}** is currently moving."

            if army.army_type != first_army_type:
                return False, "❌ You cannot merge land armies with fleets."

            # 3. Check Distance (15px Tolerance)
            dist = math.sqrt(
                (army.location_x - ref_x) ** 2 + (army.location_y - ref_y) ** 2
            )
            if dist > 15.0:
                return (
                    False,
                    f"❌ **{army.commander_name}** is too far away ({dist:.1f} px).",
                )

        # Create Coalition Shell
        coalition = Army(
            game_id=game_id,
            house_id=effective_commanding_house_id,  # Owned by the Leader House
            commander_name=new_name,
            is_coalition=True,
            army_type=first_army_type,
            troop_count=0,
            composition={},
            location_x=ref_x,
            location_y=ref_y,
            status="IDLE",
            treasury=0,
        )
        self.session.add(coalition)
        await self.session.flush()  # Generate ID

        # Calculate Totals & Create Contingents
        contingents = []
        total_comp = {}
        total_cargo_comp = {}
        total_troops = 0
        total_cargo_troops = 0
        total_gold = 0

        for a in armies_to_merge:
            contingents.append(
                ArmyContingent(
                    parent_army_id=coalition.army_id,
                    original_house_id=a.house_id,
                    troop_count=a.troop_count,
                    composition=a.composition,
                    cargo=a.cargo,
                    treasury=a.treasury or 0,
                )
            )

            # Sum stats
            total_troops += a.troop_count
            total_gold += a.treasury or 0

            for unit, count in a.composition.items():
                total_comp[unit] = total_comp.get(unit, 0) + count

            if a.cargo:
                total_cargo_troops += a.cargo.get("troop_count", 0)
                for unit, count in a.cargo.get("composition", {}).items():
                    total_cargo_comp[unit] = total_cargo_comp.get(unit, 0) + count

        # Finalize the Coalition Army
        coalition.composition = total_comp
        coalition.troop_count = total_troops
        coalition.treasury = total_gold

        if total_cargo_troops > 0:
            coalition.cargo = {
                "commander": f"Embarked forces of {new_name}",
                "troop_count": total_cargo_troops,
                "composition": total_cargo_comp,
            }

        self.session.add_all(contingents)

        # --- FIX: Cleanup interactions before deletion ---
        for a in armies_to_merge:
            # Delete any pending interactions (march, battle, meeting) linked to this army
            await self.session.execute(
                delete(PendingInteraction).where(
                    or_(
                        PendingInteraction.army1_id == a.army_id,
                        PendingInteraction.army2_id == a.army_id,
                    )
                )
            )
            # Now safe to delete
            await self.session.delete(a)

        await self.session.commit()

        unit_label = "ships" if first_army_type == "SEA" else "men"
        cargo_label = (
            f" and carrying **{total_cargo_troops} men**"
            if total_cargo_troops > 0
            else ""
        )

        return (
            True,
            f"🤝 **Coalition Formed!** The **{new_name}** has been created with **{total_troops} {unit_label}**{cargo_label}.",
        )

    async def disband_coalition(
        self,
        game_id: int,
        user_id: int,
        army_id: int,
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ):
        """
        Disbands a coalition, correctly restoring the original contingents.
        """
        # 1. VALIDATION
        coalition_army = await ArmyRepo.get_army_by_id(self.session, army_id)

        if not coalition_army:
            return False, "❌ Army ID not found."
        if not coalition_army.is_coalition:
            return False, f"❌ **{coalition_army.commander_name}** is not a coalition."

        player: GamePlayer | None = None
        player_claimed_house_id: int | None = None

        if not is_gm_override:
            stmt_p = select(GamePlayer).where(
                GamePlayer.user_id == user_id, GamePlayer.game_id == game_id
            )
            player = (await self.session.execute(stmt_p)).scalars().first()
            if not player or not player.claimed_house_id:
                return False, "❌ You do not command a house."
            player_claimed_house_id = player.claimed_house_id

        effective_commanding_house_id: int | None = None

        if is_gm_override:
            if acting_house_id is not None:
                effective_commanding_house_id = acting_house_id
            else:
                effective_commanding_house_id = coalition_army.house_id
        else:
            effective_commanding_house_id = player_claimed_house_id

        if effective_commanding_house_id is None:
            return False, "❌ Cannot determine the commanding house for this action."

        if not is_gm_override and not await self._check_command_authority(
            player, coalition_army
        ):
            return (
                False,
                f"❌ You do not have command authority over the **{coalition_army.commander_name}** coalition.",
            )

        if is_gm_override and coalition_army.house_id != effective_commanding_house_id:
            return (
                False,
                f"❌ GM override: Coalition belongs to House {coalition_army.house_id}, but you are acting as House {effective_commanding_house_id}.",
            )

        # 2. FETCH CONTINGENT DATA
        stmt_contingents = (
            select(ArmyContingent)
            .where(ArmyContingent.parent_army_id == army_id)
            .options(selectinload(ArmyContingent.original_house))
        )
        contingents = (await self.session.execute(stmt_contingents)).scalars().all()

        # Handle empty coalition edge case
        if not contingents:
            # --- FIX: Cleanup interactions before deletion ---
            await self.session.execute(
                delete(PendingInteraction).where(
                    or_(
                        PendingInteraction.army1_id == coalition_army.army_id,
                        PendingInteraction.army2_id == coalition_army.army_id,
                    )
                )
            )
            await self.session.delete(coalition_army)
            await self.session.commit()
            return (
                True,
                f"⚠️ **{coalition_army.commander_name}** was disbanded. No contingents were found to restore (Army Deleted).",
            )

        # 3. RECREATE ORIGINAL ARMIES
        report_lines = []
        unit_noun = "ships" if coalition_army.army_type == "SEA" else "men"

        for contingent in contingents:
            house_name = (
                contingent.original_house.name
                if contingent.original_house
                else "Unknown House"
            )

            restored_army = Army(
                game_id=game_id,
                house_id=contingent.original_house_id,
                commander_name=f"Reformed Host of {house_name}",
                troop_count=contingent.troop_count,
                composition=contingent.composition,
                cargo=contingent.cargo,
                treasury=contingent.treasury or 0,
                location_x=coalition_army.location_x,
                location_y=coalition_army.location_y,
                status="IDLE",
                army_type=coalition_army.army_type,
                is_coalition=False,
            )
            self.session.add(restored_army)

            gold_restored = (
                f" carrying {contingent.treasury} gold"
                if contingent.treasury and contingent.treasury > 0
                else ""
            )
            report_lines.append(
                f"Restored the forces of **House {house_name}** ({contingent.troop_count} {unit_noun}{gold_restored})."
            )

            await self.session.delete(contingent)

        # 5. CLEANUP (Coalition Army)
        # --- FIX: Cleanup interactions before deletion ---
        await self.session.execute(
            delete(PendingInteraction).where(
                or_(
                    PendingInteraction.army1_id == coalition_army.army_id,
                    PendingInteraction.army2_id == coalition_army.army_id,
                )
            )
        )
        await self.session.delete(coalition_army)

        await self.session.commit()

        final_report = "\n".join(report_lines)
        return (
            True,
            f"🏳️ **Coalition Disbanded!** The **{coalition_army.commander_name}** has been dissolved:\n{final_report}",
        )

    async def merge_armies(
        self,
        game_id: int,
        user_id: int,
        id_1: int,
        id_2: int,
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ):
        """
        Merges two same-owner armies or fleets.
        Target: Army 1 (survives). Source: Army 2 (deleted).
        """
        army1 = await ArmyRepo.get_army_by_id(self.session, id_1)
        army2 = await ArmyRepo.get_army_by_id(self.session, id_2)

        if not army1 or not army2:
            return False, "❌ One or both army IDs could not be found."

        player: GamePlayer | None = None
        player_claimed_house_id: int | None = None

        if not is_gm_override:
            stmt_p = select(GamePlayer).where(
                GamePlayer.user_id == user_id, GamePlayer.game_id == game_id
            )
            player = (await self.session.execute(stmt_p)).scalars().first()
            if not player or not player.claimed_house_id:
                return False, "❌ You do not command a house."
            player_claimed_house_id = player.claimed_house_id

        effective_commanding_house_id: int | None = None
        if is_gm_override:
            if acting_house_id is not None:
                effective_commanding_house_id = acting_house_id
            else:
                effective_commanding_house_id = army1.house_id
        else:
            effective_commanding_house_id = player_claimed_house_id

        if effective_commanding_house_id is None:
            return False, "❌ Cannot determine the commanding house for this action."

        # Authority checks
        if not is_gm_override:
            auth1 = await self._check_command_authority(player, army1)
            auth2 = await self._check_command_authority(player, army2)
            if not auth1 or not auth2:
                return False, "❌ You do not have command authority over both units."
        else:
            # GM Override Check
            if (
                army1.house_id != effective_commanding_house_id
                or army2.house_id != effective_commanding_house_id
            ):
                return (
                    False,
                    f"❌ With GM override for House ID {effective_commanding_house_id}, both armies must belong to this house.",
                )

        if army1.army_type != army2.army_type:
            return False, "❌ You cannot merge a land army with a fleet."

        # Distance Tolerance
        dist = math.sqrt(
            (army1.location_x - army2.location_x) ** 2
            + (army1.location_y - army2.location_y) ** 2
        )
        if dist > 15.0:
            return False, f"❌ Armies are too far apart (Distance: {dist:.1f})."

        if army1.status not in ["IDLE", "GARRISONED"] or army2.status not in [
            "IDLE",
            "GARRISONED",
        ]:
            return False, "❌ You can only merge idle or garrisoned units."

        unit_noun = "ships" if army1.army_type == "SEA" else "men"
        count_from_army2 = army2.troop_count
        gold_from_army2 = army2.treasury or 0
        army1.treasury = (army1.treasury or 0) + gold_from_army2

        # Snap army1 to exact position of army2 or vice-versa
        army1.location_x = army2.location_x
        army1.location_y = army2.location_y

        # --- FIX: Cleanup Interactions for Army 2 ---
        # Army 2 is about to be deleted. We must remove its locks first.
        await self.session.execute(
            delete(PendingInteraction).where(
                or_(
                    PendingInteraction.army1_id == army2.army_id,
                    PendingInteraction.army2_id == army2.army_id,
                )
            )
        )

        await ArmyRepo.merge_army_logic(
            self.session,
            source_army=army2,
            target_army=army1,
        )
        await self.session.commit()

        final_count_army1 = army1.troop_count
        gold_label = (
            f" It now holds **{army1.treasury} gold**." if army1.treasury > 0 else ""
        )

        return (
            True,
            f"✅ Merged **{count_from_army2} {unit_noun}** from **{army2.commander_name}** into **{army1.commander_name}**, which now has **{final_count_army1} {unit_noun}**.{gold_label}",
        )

    async def check_interceptions_advanced(
        self,
        game_id: int,
        army_id: int,
        path_points: list,
        start_time: datetime.datetime,
        duration: int,
    ):
        """
        Checks for collisions against IDLE armies AND MOVING armies.
        Returns list of dicts containing Time/Coords (for scheduling) AND Messages (for UI).
        """
        alerts = []
        step = 10
        radius = 75.0
        total_points = len(path_points)
        if total_points == 0:
            return []

        detected_ids = set()

        # 1. Fetch my army to avoid friendly fire
        my_army = await self.session.get(Army, army_id)
        if not my_army:
            return []
        my_house_id = my_army.house_id

        for i in range(0, total_points, step):
            px, py = path_points[i]

            progress_pct = i / total_points
            time_at_point = start_time + datetime.timedelta(
                seconds=(duration * progress_pct)
            )

            # --- A. CHECK MOVING ARMIES ---
            collision_ids = await ArmyRepo.check_trajectory_collision(
                self.session, game_id, army_id, px, py, time_at_point
            )

            for enemy_id in collision_ids:
                if enemy_id not in detected_ids:
                    enemy = await ArmyRepo.get_army_by_id(self.session, enemy_id)
                    if enemy and enemy.house_id != my_house_id:
                        est_time_str = time_at_point.strftime("%H:%M UTC")
                        region = await self._get_region_from_db(game_id, px, py)

                        msg = f"⚔️ **CONTACT!** Your scouts report **{enemy.commander_name}** is on an intercept course near **{region}** around {est_time_str}!"

                        alerts.append(
                            {
                                "enemy_id": enemy_id,  # For Scheduler
                                "enemy_army_id": enemy_id,  # For Cog
                                "time": time_at_point,  # For Scheduler
                                "coords": (px, py),  # For Scheduler
                                "message": msg,  # For UI / Test
                            }
                        )
                        detected_ids.add(enemy_id)

            # --- B. CHECK IDLE ARMIES ---
            stmt_idle = select(Army).where(
                Army.game_id == game_id,
                Army.status.in_(["IDLE", "DOCKED"]),
                Army.house_id != my_house_id,
                Army.location_x.between(px - radius, px + radius),
                Army.location_y.between(py - radius, py + radius),
            )
            idle_armies = (await self.session.execute(stmt_idle)).scalars().all()

            for enemy in idle_armies:
                if enemy.army_id not in detected_ids:
                    dist = math.sqrt(
                        (enemy.location_x - px) ** 2 + (enemy.location_y - py) ** 2
                    )
                    if dist <= radius:
                        region = await self._get_region_from_db(game_id, px, py)

                        msg = f"👀 **SIGHTING:** You will pass near **{enemy.commander_name}** (Idle) in **{region}**."

                        alerts.append(
                            {
                                "enemy_id": enemy.army_id,  # For Scheduler
                                "enemy_army_id": enemy.army_id,  # For Cog
                                "time": time_at_point,  # For Scheduler
                                "coords": (px, py),  # For Scheduler
                                "message": msg,  # For UI / Test
                            }
                        )
                        detected_ids.add(enemy.army_id)

        return alerts

    async def embark_army(
        self,
        game_id: int,
        user_id: int,
        land_army_id: int,
        fleet_id: int,
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ):
        land_army = await ArmyRepo.get_army_by_id(self.session, land_army_id)
        fleet = await ArmyRepo.get_army_by_id(self.session, fleet_id)
        if not land_army or not fleet:
            return False, "❌ Unit not found."

        game = await self.session.get(Game, game_id)
        if not game:
            return False, "❌ Game session not found."

        player: GamePlayer | None = None
        player_claimed_house_id: int | None = None
        if not is_gm_override:
            player = await self.session.scalar(
                select(GamePlayer).where(
                    GamePlayer.user_id == user_id, GamePlayer.game_id == game_id
                )
            )
            if not player or not player.claimed_house_id:
                return False, "❌ You do not command a house.", None
            player_claimed_house_id = player.claimed_house_id

        effective_commanding_house_id: int | None = None
        if is_gm_override:
            if acting_house_id is not None:
                effective_commanding_house_id = acting_house_id
            else:
                effective_commanding_house_id = (
                    land_army.house_id
                )  # Assume GM acts for the land army's house
        else:
            effective_commanding_house_id = player_claimed_house_id

        if effective_commanding_house_id is None:
            return (
                False,
                "❌ Cannot determine the commanding house for this action.",
                None,
            )

        if not is_gm_override and (
            not player
            or land_army.house_id != player_claimed_house_id
            or fleet.house_id != player_claimed_house_id
        ):
            return False, "❌ Not your armies."

        # If GM is overriding, ensure the armies belong to the effective_commanding_house_id
        if is_gm_override and (
            land_army.house_id != effective_commanding_house_id
            or fleet.house_id != effective_commanding_house_id
        ):
            return (
                False,
                f"❌ GM override: Both units must belong to the specified acting house ID {effective_commanding_house_id}.",
                None,
            )

        if land_army.army_type != "LAND" or fleet.army_type != "SEA":
            return False, "❌ Must embark LAND into SEA."

        # FIX: DISTANCE TOLERANCE
        dist = math.sqrt(
            (land_army.location_x - fleet.location_x) ** 2
            + (land_army.location_y - fleet.location_y) ** 2
        )
        if dist > 15.0:  # Allow 15px margin of error
            return (
                False,
                f"❌ Units are too far apart (Distance: {dist:.1f}). They must be at the same location.",
            )

        if fleet.cargo:
            return False, "❌ Fleet already has cargo."
        if fleet.status not in ["IDLE", "DOCKED"]:
            return False, "❌ The fleet must be stationary to embark troops."

        ship_capacity = game.ship_capacity
        capacity = fleet.troop_count * ship_capacity
        if land_army.troop_count > capacity:
            return (
                False,
                f"❌ Not enough capacity! Fleet: {capacity}, Army: {land_army.troop_count}.",
            )

        fleet.cargo = {
            "commander": land_army.commander_name,
            "troop_count": land_army.troop_count,
            "composition": land_army.composition,
        }

        # OPTIONAL: Sync fleet coordinates to land army to clean up slight drifts
        fleet.location_x = land_army.location_x
        fleet.location_y = land_army.location_y

        await self.session.delete(land_army)
        await self.session.commit()
        return (
            True,
            f"✅ **Embarked!** {land_army.commander_name} ({land_army.troop_count} men) boarded the {fleet.commander_name}.",
        )

    async def disembark_army(
        self,
        game_id: int,
        user_id: int,
        army_id: int,
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ):
        """
        Unloads cargo from a fleet.
        Includes "Snap to Land" to prevent troops from drowning in water tiles.
        FIX: Now allows disembarking directly if the fleet is at a PORT.
        """
        # 1. Validation
        fleet = await ArmyRepo.get_army_by_id(self.session, army_id)
        if not fleet or fleet.army_type != "SEA":
            return False, "❌ Not a fleet."

        player: GamePlayer | None = None
        player_claimed_house_id: int | None = None
        if not is_gm_override:
            player = await self.session.scalar(
                select(GamePlayer).where(
                    GamePlayer.user_id == user_id, GamePlayer.game_id == game_id
                )
            )
            if not player or not player.claimed_house_id:
                return False, "❌ You do not command a house."
            player_claimed_house_id = player.claimed_house_id

        effective_commanding_house_id: int | None = None
        if is_gm_override:
            if acting_house_id is not None:
                effective_commanding_house_id = acting_house_id
            else:
                effective_commanding_house_id = (
                    fleet.house_id
                )  # Assume GM acts for the fleet's house
        else:
            effective_commanding_house_id = player_claimed_house_id

        if effective_commanding_house_id is None:
            return (
                False,
                "❌ Cannot determine the commanding house for this action.",
            )

        if not is_gm_override and (
            not player or fleet.house_id != player_claimed_house_id
        ):
            return False, "❌ Not your fleet."

        # If GM is overriding, ensure the fleet belongs to the effective_commanding_house_id
        if is_gm_override and fleet.house_id != effective_commanding_house_id:
            return (
                False,
                f"❌ GM override: Fleet {fleet.commander_name} does not belong to the specified acting house ID {effective_commanding_house_id}.",
            )

        # Allow DOCKED or IDLE, but not SAILING
        if fleet.status not in ["IDLE", "DOCKED", "GARRISONED"]:
            return False, "❌ The fleet must be stationary to disembark troops."

        if not fleet.cargo or fleet.cargo.get("troop_count", 0) <= 0:
            return False, "❌ Fleet has no cargo."

        # 2. FIND LAND SCAN (Snap Logic)
        best_land_spot = None
        min_dist_sq = float("inf")
        original_x, original_y = int(fleet.location_x), int(fleet.location_y)

        # Access the Global Pathfinder Engine
        cost_map = PF_ENGINE.cost_map
        rows, cols = cost_map.shape

        # --- CHECK 1: Is the current tile a PORT? ---
        # If we are at a port, we can dump troops right here.
        if 0 <= original_y < rows and 0 <= original_x < cols:
            current_cost = cost_map[original_y, original_x]
            if current_cost == COSTS["port"]:
                best_land_spot = (original_x, original_y)

        # --- CHECK 2: If not a port, scan neighbors for Land/Road ---
        if not best_land_spot:
            # Loop dx, dy (-1 to +1)
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    check_x, check_y = original_x + dx, original_y + dy

                    # Bounds Check
                    if 0 <= check_y < rows and 0 <= check_x < cols:
                        terrain_cost = cost_map[check_y, check_x]

                        # Check if tile is Land or Road (or Port adjacent)
                        is_water = terrain_cost in [
                            COSTS["ocean"],
                            COSTS["coastal_water"],
                        ]

                        # Note: We treat PORT as landable for disembarking
                        if not is_water:
                            dist_sq = dx * dx + dy * dy
                            # Prefer closest tile
                            if dist_sq < min_dist_sq:
                                min_dist_sq = dist_sq
                                best_land_spot = (check_x, check_y)

        # 3. Handle Result
        if not best_land_spot:
            return False, (
                f"❌ **Cannot Disembark Here!**\n"
                f"The fleet is currently at {original_x},{original_y} (Water), and no valid land or port was found in the adjacent tiles.\n"
                f"Please use `!sail` to move the fleet adjacent to a coast/beach before disembarking."
            )

        # 4. Create the Army at the Found Land Spot
        land_x, land_y = best_land_spot
        cargo = fleet.cargo

        # Check Fief status for "GARRISONED" vs "IDLE"
        stmt_fief = select(Fief).where(
            Fief.game_id == game_id,
            Fief.location_x == land_x,
            Fief.location_y == land_y,
        )
        fief = (await self.session.execute(stmt_fief)).scalars().first()
        land_status = (
            "GARRISONED"
            if fief and fief.owner_id == effective_commanding_house_id
            else "IDLE"
        )

        new_army = Army(
            game_id=fleet.game_id,
            house_id=effective_commanding_house_id,
            army_type="LAND",
            commander_name=cargo.get("commander", "Reformed Host"),
            troop_count=cargo.get("troop_count", 0),
            composition=cargo.get("composition", {}),
            location_x=land_x,  # Snap coord
            location_y=land_y,  # Snap coord
            status=land_status,
        )
        self.session.add(new_army)

        # Clear Cargo
        fleet.cargo = None

        await self.session.commit()

        location_desc = f"{fief.name}" if fief else f"Coord {land_x}, {land_y}"

        return (
            True,
            f"✅ **Disembarked!** {new_army.commander_name} ({new_army.troop_count} men) made landfall at **{location_desc}**.",
        )

    async def recruit_troops(
        self,
        game_id: int,
        user_id: int,
        fief_name: str,
        amount: int,
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ) -> tuple[bool, str]:
        """
        Turns Manpower into Troops at a specific Fief, respecting the game's
        manpower rules.
        """
        game = await self.session.get(Game, game_id)
        if not game:
            return False, "❌ Internal error: Game not found."

        # 1. Validation
        player: GamePlayer | None = None
        player_claimed_house_id: int | None = None
        if not is_gm_override:
            player_stmt = (
                select(GamePlayer)
                .where(GamePlayer.user_id == user_id, GamePlayer.game_id == game_id)
                .options(selectinload(GamePlayer.house))
            )
            player = (await self.session.execute(player_stmt)).scalars().first()
            if not player or not player.is_primary:
                return False, "❌ Only the Head of House can recruit."
            player_claimed_house_id = player.house.house_id

        effective_commanding_house_id: int | None = None
        if is_gm_override:
            if acting_house_id is None:
                return (
                    False,
                    "❌ GM override requires an acting house ID for recruitment.",
                    None,
                )
            effective_commanding_house_id = acting_house_id
        else:
            effective_commanding_house_id = player_claimed_house_id

        if effective_commanding_house_id is None:
            return (
                False,
                "❌ Cannot determine the commanding house for this action.",
                None,
            )

        house = await self.session.get(House, effective_commanding_house_id)
        if not house:
            return (
                False,
                f"❌ House ID {effective_commanding_house_id} not found.",
                None,
            )

        # Wrap manpower logic in a conditional block.
        manpower_message = "Manpower was not a factor."
        if game.manpower_enabled:
            if not is_gm_override:  # Only check manpower for players
                if house.manpower < amount:
                    return (
                        False,
                        f"❌ Not enough manpower! Available: **{house.manpower}**, Needed: **{amount}**.",
                    )
                # Deduct Manpower for players
                house.manpower -= amount
                manpower_message = f"Your manpower pool is now **{house.manpower}**."
            else:  # GM override, bypass manpower limits but still show message
                manpower_message = "Manpower check bypassed by GM override."

        # Find Fief
        stmt_f = select(Fief).where(
            Fief.game_id == game_id,
            Fief.name.ilike(fief_name),
            Fief.owner_id == effective_commanding_house_id,
        )
        fief = (await self.session.execute(stmt_f)).scalars().first()
        if not fief:
            return False, f"❌ You do not own a fief named **{fief_name}**."

        # 3. Create Composition based on Region
        comp_ratios = {
            "The North": {"infantry": 0.70, "archers": 0.15, "cavalry": 0.15},
            "The Crownlands": {"infantry": 0.60, "archers": 0.30, "cavalry": 0.10},
            "The Iron Islands": {"infantry": 0.60, "archers": 0.30, "cavalry": 0.10},
            "The Reach": {"infantry": 0.50, "archers": 0.20, "cavalry": 0.30},
            "The Riverlands": {"infantry": 0.50, "archers": 0.25, "cavalry": 0.25},
            "The Westerlands": {"infantry": 0.55, "archers": 0.20, "cavalry": 0.25},
            "The Vale": {"infantry": 0.40, "archers": 0.30, "cavalry": 0.30},
            "Dorne": {"infantry": 0.50, "archers": 0.20, "cavalry": 0.30},
            "The Stormlands": {"infantry": 0.60, "archers": 0.25, "cavalry": 0.15},
            "The Narrow Sea": {"infantry": None, "archers": None, "cavalry": None},
            "Red Waste": {"infantry": None, "archers": None, "cavalry": None},
            "The Wall": {"infantry": None, "archers": None, "cavalry": None},
            "The Far East": {"infantry": None, "archers": None, "cavalry": None},
            "North Essos": {"infantry": None, "archers": None, "cavalry": None},
            "Summer Isles": {"infantry": None, "archers": None, "cavalry": None},
            "Slaver's Bay": {"infantry": None, "archers": None, "cavalry": None},
            "Beyond the Wall": {"infantry": None, "archers": None, "cavalry": None},
            "The Free Cities": {"infantry": None, "archers": None, "cavalry": None},
            "Skagos": {"infantry": None, "archers": None, "cavalry": None},
            "Valyria": {"infantry": None, "archers": None, "cavalry": None},
            "Dothraki Sea": {"infantry": None, "archers": None, "cavalry": None},
        }
        ratios = comp_ratios.get(
            fief.region, {"infantry": 0.6, "archers": 0.3, "cavalry": 0.1}
        )
        new_comp = {
            "infantry": int(amount * ratios.get("infantry", 0)),
            "cavalry": int(amount * ratios.get("cavalry", 0)),
            "archers": int(amount * ratios.get("archers", 0)),
        }
        current_total = sum(new_comp.values())
        remainder = amount - current_total
        if remainder > 0:
            new_comp["infantry"] += remainder

        # 4. Add to Garrison
        stmt_g = select(Army).where(
            Army.house_id == effective_commanding_house_id,
            Army.location_x == fief.location_x,
            Army.location_y == fief.location_y,
            Army.status == "GARRISONED",
        )
        garrison = (await self.session.execute(stmt_g)).scalars().first()

        if not garrison:
            garrison = Army(
                game_id=game_id,
                house_id=effective_commanding_house_id,
                army_type="LAND",
                commander_name=f"Garrison of {fief.name}",
                troop_count=0,
                composition={},
                location_x=fief.location_x,
                location_y=fief.location_y,
                status="GARRISONED",
            )
            self.session.add(garrison)

        garrison.troop_count += amount
        for unit, count in new_comp.items():
            garrison.composition[unit] = garrison.composition.get(unit, 0) + count

        flag_modified(garrison, "composition")

        await self.session.commit()

        # Use the dynamic message in the final output.
        return (
            True,
            f"✅ **Recruited {amount} men** at **{fief.name}**. {manpower_message}",
        )

    def _find_closest_coastal_landing(
        self, target_x: int, target_y: int, max_radius: int = 50
    ):
        """
        Scans outward from a target point (potentially inland) to find the nearest coastal water tile
        that allows for a valid army landing.

        Returns:
            (water_x, water_y): Where the fleet should stop.
            (land_x, land_y): Where the army should disembark (adjacent to water).
        """
        if PF_ENGINE.cost_map is None:
            return None, None

        rows, cols = PF_ENGINE.cost_map.shape
        # Ensure target_x, target_y are within map bounds for initial check
        if not (0 <= target_y < rows and 0 <= target_x < cols):
            return None, None

        WATER_TILES = [COSTS["ocean"], COSTS["coastal_water"], COSTS["port"]]
        LAND_TILES = [
            COSTS["land"],
            COSTS["road"],
        ]  # Explicitly define what counts as land
        IMPASSABLE_TILE = 255  # Assuming 255 is general impassable terrain

        # --- NEW CRITICAL LOGIC: Immediate check if target is already water/port ---
        target_terrain_cost = PF_ENGINE.cost_map[target_y, target_x]

        if target_terrain_cost in WATER_TILES:
            # If the target itself is a water tile (like a Port or Coastal Water),
            # it's a potential landing point for the fleet. Now, find the closest adjacent LAND.
            best_adjacent_land = None
            min_dist_sq_adj = float("inf")

            # Search 8 directions for adjacent land from the target water tile
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    if dx == 0 and dy == 0:
                        continue  # Skip self

                    nx, ny = target_x + dx, target_y + dy
                    if 0 <= ny < rows and 0 <= nx < cols:
                        neighbor_terrain_cost = PF_ENGINE.cost_map[ny, nx]
                        if neighbor_terrain_cost in LAND_TILES:
                            dist_sq = dx * dx + dy * dy
                            if dist_sq < min_dist_sq_adj:
                                min_dist_sq_adj = dist_sq
                                best_adjacent_land = (nx, ny)

            if best_adjacent_land:
                # Found land directly adjacent to the target water tile. This is our landing spot.
                print(
                    f"DEBUG: Target {target_x},{target_y} is water/port. Found adjacent land at {best_adjacent_land}."
                )
                return (target_x, target_y), best_adjacent_land
            else:
                # Target is water/port but no adjacent land. This is an unusual situation.
                # It means the Port is "landlocked" by other water tiles or impassable terrain.
                # In this case, we proceed with the BFS to search further out.
                print(
                    f"DEBUG: Target {target_x},{target_y} is water/port but no adjacent land. Initiating wider BFS search."
                )

        # --- Original BFS logic (for when target is inland or adjacent land was not immediately found) ---
        visited = set()
        queue = collections.deque([(target_x, target_y)])
        visited.add((target_x, target_y))

        directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]  # Cardinal directions for BFS

        while queue:
            cx, cy = queue.popleft()

            # Stop if we've gone too far (performance safety)
            if abs(cx - target_x) > max_radius or abs(cy - target_y) > max_radius:
                continue

            # Check neighbors
            for dx, dy in directions:
                nx, ny = cx + dx, cy + dy

                if 0 <= ny < rows and 0 <= nx < cols:
                    if (nx, ny) in visited:
                        continue

                    neighbor_terrain_cost = PF_ENGINE.cost_map[ny, nx]

                    # FOUND WATER (nx, ny) ADJACENT TO LAND (cx, cy)?
                    if neighbor_terrain_cost in WATER_TILES:
                        # Ensure cx,cy is actually a land tile (not water, not impassable)
                        current_terrain_cost = PF_ENGINE.cost_map[cy, cx]
                        if current_terrain_cost in LAND_TILES:
                            return (nx, ny), (
                                cx,
                                cy,
                            )  # Fleet stops at nx,ny, army disembarks at cx,cy
                        # If cx,cy is also water or impassable, this isn't the transition we're looking for, continue search.
                        continue

                    # If it's valid land (not water, not impassable), add to queue to keep searching
                    if (
                        neighbor_terrain_cost in LAND_TILES
                    ):  # Only add land tiles to queue for this search type
                        visited.add((nx, ny))
                        queue.append((nx, ny))

        return None, None  # No coastal landing spot found within radius

    def _is_coord_water_or_port(self, x: int, y: int) -> bool:
        """Checks if a given coordinate is a water or port tile on the cost map."""
        if not (0 <= y < self.cost_map.shape[0] and 0 <= x < self.cost_map.shape[1]):
            return False
        terrain_cost = self.cost_map[y, x]
        return terrain_cost in [COSTS["ocean"], COSTS["coastal_water"], COSTS["port"]]

    def _find_landing_zone_bfs(
        self, target_x: int, target_y: int, max_radius: int = 300
    ):
        """
        The "Wide Search".
        Given a target coordinate (usually a Fief center):
        1. If it's Land: Search outward for the nearest Water/Port.
        2. If it's Water: Search outward for the nearest Land.
        Returns: (water_x, water_y), (land_x, land_y)
        """
        if self.cost_map is None:
            return None, None

        rows, cols = self.cost_map.shape
        if not (0 <= target_y < rows and 0 <= target_x < cols):
            return None, None

        target_val = self.cost_map[target_y, target_x]

        WATER_TILES = [COSTS["ocean"], COSTS["coastal_water"], COSTS["port"]]
        LAND_TILES = [COSTS["land"], COSTS["road"]]  # Add others if needed
        IMPASSABLE = 255

        start_is_water = target_val in WATER_TILES

        # BFS Setup
        visited = set()
        queue = collections.deque([(target_x, target_y)])
        visited.add((target_x, target_y))

        # 8-Directional search for better coverage
        directions = [
            (-1, 0),
            (1, 0),
            (0, -1),
            (0, 1),
            (-1, -1),
            (-1, 1),
            (1, -1),
            (1, 1),
        ]

        while queue:
            cx, cy = queue.popleft()

            # Safety break
            if abs(cx - target_x) > max_radius or abs(cy - target_y) > max_radius:
                continue

            current_val = self.cost_map[cy, cx]

            # CHECK NEIGHBORS
            for dx, dy in directions:
                nx, ny = cx + dx, cy + dy

                if 0 <= ny < rows and 0 <= nx < cols:
                    if (nx, ny) in visited:
                        continue

                    neighbor_val = self.cost_map[ny, nx]

                    # LOGIC A: Started on Land, looking for Water
                    if not start_is_water:
                        # If neighbor is water, we found the coast!
                        if neighbor_val in WATER_TILES:
                            # Current (cx, cy) is Land (or the path we took to get here).
                            # Neighbor (nx, ny) is Water.
                            return (nx, ny), (cx, cy)

                        # If neighbor is passable land, keep searching
                        if neighbor_val < IMPASSABLE:
                            visited.add((nx, ny))
                            queue.append((nx, ny))

                    # LOGIC B: Started on Water (Port Fief), looking for Land
                    else:
                        # If neighbor is Land, we found the shore!
                        if neighbor_val in LAND_TILES:
                            # Current (cx, cy) is Water.
                            # Neighbor (nx, ny) is Land.
                            return (cx, cy), (nx, ny)

                        # If neighbor is water, keep searching
                        if neighbor_val in WATER_TILES:
                            visited.add((nx, ny))
                            queue.append((nx, ny))

        return None, None

    def _find_closest_land_to_coord(
        self, target_x: int, target_y: int, max_radius: int = 50
    ):
        """
        Scans outward from a target coordinate (potentially water) to find the nearest *land* tile.
        Used to resolve a land army's final destination to a valid land tile.
        """
        if PF_ENGINE.cost_map is None:
            return None

        rows, cols = PF_ENGINE.cost_map.shape
        visited = set()
        queue = collections.deque([(target_x, target_y)])
        visited.add((target_x, target_y))

        directions = [
            (-1, 0),
            (1, 0),
            (0, -1),
            (0, 1),
            (-1, -1),
            (-1, 1),
            (1, -1),
            (1, 1),
        ]  # 8 directions for land search

        WATER_OR_IMPASSABLE_TILES = [
            COSTS["ocean"],
            COSTS["coastal_water"],
            COSTS[
                "river_wall"
            ],  # Rivers might be impassable for land, depending on settings
            255,  # Assuming 255 is general impassable
        ]

        while queue:
            cx, cy = queue.popleft()

            # If current tile is already land (and not impassable), return it
            if 0 <= cy < rows and 0 <= cx < cols:
                terrain_cost = PF_ENGINE.cost_map[cy, cx]
                if terrain_cost not in WATER_OR_IMPASSABLE_TILES:
                    return (cx, cy)

            # Stop if we've gone too far
            if abs(cx - target_x) > max_radius or abs(cy - target_y) > max_radius:
                continue

            for dx, dy in directions:
                nx, ny = cx + dx, cy + dy

                if 0 <= ny < rows and 0 <= nx < cols:
                    if (nx, ny) in visited:
                        continue

                    terrain_cost = PF_ENGINE.cost_map[ny, nx]

                    # If it's valid land (not water, not impassable), return it
                    if terrain_cost not in WATER_OR_IMPASSABLE_TILES:
                        return (nx, ny)  # Found closest land tile

                    # If it's not impassable, add to queue to keep searching
                    if (
                        terrain_cost < 255
                    ):  # Also add water tiles to search if they might lead to land, but don't return them as land
                        visited.add((nx, ny))
                        queue.append((nx, ny))
        return None  # No land found within radius

    def _is_coord_water_or_port(self, x: int, y: int) -> bool:
        """Checks if a given coordinate is a water or port tile on the cost map."""
        if not (0 <= y < self.cost_map.shape[0] and 0 <= x < self.cost_map.shape[1]):
            return False
        terrain_cost = self.cost_map[y, x]
        return terrain_cost in [COSTS["ocean"], COSTS["coastal_water"], COSTS["port"]]

    def _find_closest_coastal_landing(
        self, target_x: int, target_y: int, max_radius: int = 300
    ):
        """
        Scans outward from a target point (potentially inland or water) to find:
        1. The nearest Water/Port tile (fleet stop).
        2. An adjacent Land tile (army disembarkation).
        Returns: (water_x, water_y), (land_x, land_y)
        """
        if self.cost_map is None:
            return None, None

        rows, cols = self.cost_map.shape
        if not (0 <= target_y < rows and 0 <= target_x < cols):
            return None, None

        target_val = self.cost_map[target_y, target_x]

        WATER_TILES = [COSTS["ocean"], COSTS["coastal_water"], COSTS["port"]]
        LAND_TILES = [COSTS["land"], COSTS["road"]]

        # --- Check if target is already water/port ---
        if target_val in WATER_TILES:
            best_adjacent_land = None
            min_dist_sq_adj = float("inf")
            directions_adj = [
                (-1, 0),
                (1, 0),
                (0, -1),
                (0, 1),
                (-1, -1),
                (-1, 1),
                (1, -1),
                (1, 1),
            ]

            for dx, dy in directions_adj:
                nx, ny = target_x + dx, target_y + dy
                if 0 <= ny < rows and 0 <= nx < cols:
                    neighbor_terrain_cost = self.cost_map[ny, nx]
                    if neighbor_terrain_cost in LAND_TILES:
                        dist_sq = dx * dx + dy * dy
                        if dist_sq < min_dist_sq_adj:
                            min_dist_sq_adj = dist_sq
                            best_adjacent_land = (nx, ny)

            if best_adjacent_land:
                return (
                    target_x,
                    target_y,
                ), best_adjacent_land  # Target is water, found adjacent land

        # --- BFS setup for inland destinations or water surrounded by water ---
        visited = set()
        queue = collections.deque([(target_x, target_y)])
        visited.add((target_x, target_y))

        directions_bfs = [
            (-1, 0),
            (1, 0),
            (0, -1),
            (0, 1),
        ]  # Cardinal directions for BFS

        while queue:
            cx, cy = queue.popleft()
            current_val = self.cost_map[cy, cx]

            # Stop if too far
            if abs(cx - target_x) > max_radius or abs(cy - target_y) > max_radius:
                continue

            # Check neighbors
            for dx, dy in directions_bfs:
                nx, ny = cx + dx, cy + dy

                if 0 <= ny < rows and 0 <= nx < cols:
                    if (nx, ny) in visited:
                        continue

                    neighbor_val = self.cost_map[ny, nx]

                    # If neighbor is water AND current is land -> Found Transition!
                    if neighbor_val in WATER_TILES and current_val in LAND_TILES:
                        return (nx, ny), (
                            cx,
                            cy,
                        )  # Fleet stops at water (nx,ny), army lands at land (cx,cy)

                    # If neighbor is passable land, add to queue to continue search inland
                    if neighbor_val in LAND_TILES:
                        visited.add((nx, ny))
                        queue.append((nx, ny))

        return None, None  # No landing zone found

    def _find_closest_land_to_coord(
        self, target_x: int, target_y: int, max_radius: int = 50
    ):
        """
        Scans outward from a target coordinate (potentially water) to find the nearest *land* tile.
        Used to resolve a land army's final destination to a valid land tile.
        """
        if PF_ENGINE.cost_map is None:
            return None

        rows, cols = PF_ENGINE.cost_map.shape
        visited = set()
        queue = collections.deque([(target_x, target_y)])
        visited.add((target_x, target_y))

        directions = [
            (-1, 0),
            (1, 0),
            (0, -1),
            (0, 1),
            (-1, -1),
            (-1, 1),
            (1, -1),
            (1, 1),
        ]

        WATER_OR_IMPASSABLE_TILES = [
            COSTS["ocean"],
            COSTS["coastal_water"],
            COSTS["port"],
            COSTS["river_wall"],
            255,
        ]

        while queue:
            cx, cy = queue.popleft()

            if 0 <= cy < rows and 0 <= cx < cols:
                terrain_cost = PF_ENGINE.cost_map[cy, cx]
                if terrain_cost not in WATER_OR_IMPASSABLE_TILES:
                    return (cx, cy)

            if abs(cx - target_x) > max_radius or abs(cy - target_y) > max_radius:
                continue

            for dx, dy in directions:
                nx, ny = cx + dx, cy + dy
                if 0 <= ny < rows and 0 <= nx < cols:
                    if (nx, ny) in visited:
                        continue
                    visited.add((nx, ny))
                    # Only search if it's passable (even if water, to bridge gaps)
                    if PF_ENGINE.cost_map[ny, nx] < 255:
                        queue.append((nx, ny))
        return None

    async def sail_fleet(
        self,
        game_id: int,
        user_id: int,
        fleet_id: int,
        ships_input: str,
        dest_name: str,
        units_input: str | None,
        commander: str | None,
        gold_to_carry: int = 0,
        waypoints: str | None = None,
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ):
        """
        Handles sailing logic using the 'Pending March' system.
        Troops stay on ships until arrival at the landing zone.
        """
        from sqlalchemy.orm.attributes import (
            flag_modified,
        )  # Absolute fix for UnboundLocalError

        # =================================================================
        # 1. VALIDATION AND SETUP
        # =================================================================
        gm_settings, _ = await self._get_gm_settings_from_game(game_id)
        game = await self.session.get(Game, game_id)
        if not game:
            return False, "❌ Game session not found.", None

        ship_capacity = game.ship_capacity
        source_fleet = await ArmyRepo.get_army_by_id(self.session, fleet_id)
        if not source_fleet:
            return False, f"❌ Fleet ID {fleet_id} not found.", None

        # Determine authority and effective house
        player_claimed_house_id = None
        if not is_gm_override:
            player = await self.session.scalar(
                select(GamePlayer).where(
                    GamePlayer.user_id == user_id, GamePlayer.game_id == game_id
                )
            )
            if not player or not player.claimed_house_id:
                return False, "❌ You do not command a house.", None
            player_claimed_house_id = player.claimed_house_id

        effective_house_id = (
            acting_house_id if is_gm_override else player_claimed_house_id
        )
        if effective_house_id is None:
            effective_house_id = source_fleet.house_id

        if source_fleet.army_type != "SEA":
            return False, "❌ This is not a fleet. Please use `!march`.", None
        if source_fleet.status in ["SAILING", "MARCHING"]:
            return False, "❌ This fleet is already moving.", None

        # =================================================================
        # 2. RESOLVE DESTINATION & PATHFINDING
        # =================================================================
        origin_name = await self.get_location_name_from_coords(
            game_id, source_fleet.location_x, source_fleet.location_y
        )
        dest_coords_raw = await self._get_location_from_db(game_id, dest_name)
        if not dest_coords_raw:
            return False, f"❌ Destination '{dest_name}' is invalid.", None

        start_coords = (source_fleet.location_x, source_fleet.location_y)
        parsed_waypoints = (
            [wp.strip() for wp in waypoints.split(";")] if waypoints else []
        )

        # Check if final destination is a port or deep water
        dest_is_water = self._is_coord_water_or_port(
            int(dest_coords_raw["x"]), int(dest_coords_raw["y"])
        )

        needs_hybrid_journey = not dest_is_water
        sea_path_points_for_log = []
        land_path_data = None
        fleet_final_dest_coords = (dest_coords_raw["x"], dest_coords_raw["y"])
        journey_summary = ""

        if needs_hybrid_journey:
            # 1. Find a coastal transition point
            best_water_loc, best_land_loc = self._find_closest_coastal_landing(
                int(dest_coords_raw["x"]), int(dest_coords_raw["y"])
            )
            if not best_water_loc:
                return (
                    False,
                    f"❌ Could not find a suitable landing spot near **{dest_name}**.",
                    None,
                )

            # 2. Calculate Sea Leg
            path_data = await PF_ENGINE.find_journey_async(
                start_loc=start_coords,
                end_loc=best_water_loc,
                waypoints=parsed_waypoints,
                travel_mode="sea_only",
                gm_settings=gm_settings,
            )
            if not path_data:
                return False, "❌ Cannot find a viable sea route to the coast.", None

            # 3. Calculate Land Leg
            land_path_data = await PF_ENGINE.find_journey_async(
                start_loc=best_land_loc,
                end_loc=(dest_coords_raw["x"], dest_coords_raw["y"]),
                waypoints=[],
                travel_mode="land_only",
                gm_settings=gm_settings,
            )
            if not land_path_data:
                return (
                    False,
                    "❌ Cannot find a land route from the coast to the target.",
                    None,
                )

            sea_path_points_for_log = path_data["path_points"]
            fleet_final_dest_coords = best_water_loc
            journey_summary = f"Sailing to the coast, then marching to **{dest_name}**."
        else:
            # Pure Sea Journey
            path_data = await PF_ENGINE.find_journey_async(
                start_loc=start_coords,
                end_loc=fleet_final_dest_coords,
                waypoints=parsed_waypoints,
                travel_mode="sea_only",
                gm_settings=gm_settings,
            )
            if not path_data:
                return False, "❌ No viable sea path found.", None

            sea_path_points_for_log = path_data["path_points"]
            journey_summary = f"Sailing directly to **{dest_name}**."

        # Map Visualization
        path_data["image"] = await asyncio.to_thread(
            self._generate_path_image,
            path_data["path_points"],
            [start_coords, fleet_final_dest_coords],
        )

        # =================================================================
        # 3. DETERMINE CARGO & TROOPS
        # =================================================================
        try:
            ship_count = (
                int(ships_input)
                if ships_input.lower() != "all"
                else source_fleet.troop_count
            )
            if ship_count <= 0 or ship_count > source_fleet.troop_count:
                raise ValueError("Invalid ship count.")

            total_men_in_cargo = 0
            cargo_payload = None

            # Look for ground army to pick up
            stmt_land = select(Army).where(
                Army.game_id == game_id,
                Army.house_id == effective_house_id,
                Army.location_x == source_fleet.location_x,
                Army.location_y == source_fleet.location_y,
                Army.army_type == "LAND",
                Army.status.in_(["IDLE", "GARRISONED"]),
            )
            ground_army = (await self.session.execute(stmt_land)).scalars().first()

            if units_input is not None and units_input.lower() != "load":
                # CASE A: Manually loading specific units from a garrison
                if not ground_army:
                    return (
                        False,
                        "❌ No land troops found at this location to load.",
                        None,
                    )

                parsed_men, requested_comp = await self._parse_units_for_sailing(
                    units_input
                )
                if parsed_men > ship_count * ship_capacity:
                    raise ValueError(
                        f"Over Capacity. Ships can only carry {ship_count * ship_capacity} men."
                    )

                # Deduct from ground host
                ground_army.troop_count -= parsed_men
                for k, v in requested_comp.items():
                    ground_army.composition[k] = ground_army.composition.get(k, 0) - v

                if ground_army.troop_count <= 0:
                    await self.session.delete(ground_army)
                else:
                    flag_modified(ground_army, "composition")

                total_men_in_cargo = parsed_men
                cargo_payload = {
                    "commander": commander or f"Host of {ground_army.commander_name}",
                    "troop_count": parsed_men,
                    "composition": requested_comp,
                }

            elif source_fleet.cargo and source_fleet.cargo.get("troop_count", 0) > 0:
                # CASE B: Maintain existing cargo (Continuing a journey)
                total_men_in_cargo = source_fleet.cargo.get("troop_count", 0)
                cargo_payload = copy.deepcopy(source_fleet.cargo)

            elif ground_army:
                # CASE C: Auto-pickup everything
                if ground_army.troop_count > ship_count * ship_capacity:
                    raise ValueError(
                        "Not enough ship capacity to pick up the entire army."
                    )
                total_men_in_cargo = ground_army.troop_count
                cargo_payload = {
                    "commander": ground_army.commander_name,
                    "troop_count": ground_army.troop_count,
                    "composition": ground_army.composition,
                }
                await self.session.delete(ground_army)

        except ValueError as e:
            return False, f"❌ Input Error: {e}", None

        # =================================================================
        # 4. PREPARE FLEET & GOLD
        # =================================================================
        stmt_house = (
            select(House)
            .where(House.house_id == effective_house_id)
            .options(selectinload(House.fiefs))
        )
        house_obj = (await self.session.execute(stmt_house)).scalar_one_or_none()
        if not house_obj:
            return False, f"❌ House ID {effective_house_id} not found.", None
        house_region = None
        if house_obj.fiefs:
            # Assuming the house's primary region is its first fief's region
            house_region = house_obj.fiefs[0].region
        if gold_to_carry > (house_obj.treasury or 0):
            return False, f"❌ House {house_obj.name} treasury insufficient.", None

        house_obj.treasury -= gold_to_carry

        fleet_to_sail = source_fleet
        if ship_count < source_fleet.troop_count:
            fleet_to_sail = await ArmyRepo.split_army_logic(
                self.session,
                source_fleet,
                ship_count,
                commander or f"Fleet of {house_obj.name}",
            )
        else:
            if commander:
                fleet_to_sail.commander_name = commander

        fleet_to_sail.cargo = cargo_payload
        fleet_to_sail.treasury = (fleet_to_sail.treasury or 0) + gold_to_carry

        # =================================================================
        # 5. EXECUTION & PENDING MARCH
        # =================================================================
        now = datetime.datetime.now(datetime.timezone.utc)
        sea_dur = calculate_travel_duration(
            path_data["terrain_breakdown"], ship_count, house_region=house_region
        )
        arrival = now + datetime.timedelta(seconds=sea_dur)

        fleet_to_sail.destination_x, fleet_to_sail.destination_y = (
            fleet_final_dest_coords
        )
        (
            fleet_to_sail.status,
            fleet_to_sail.departure_time,
            fleet_to_sail.arrival_time,
        ) = ("SAILING", now, arrival)

        if needs_hybrid_journey and total_men_in_cargo > 0:
            # We store the land data in cargo instead of creating the army now
            land_dur = calculate_travel_duration(
                land_path_data["terrain_breakdown"], total_men_in_cargo
            )
            fleet_to_sail.cargo["pending_march"] = {
                "dest_x": dest_coords_raw["x"],
                "dest_y": dest_coords_raw["y"],
                "path": land_path_data["path_points"],
                "duration": land_dur,
            }
            flag_modified(fleet_to_sail, "cargo")

        await ArmyRepo.log_march_path(
            self.session,
            fleet_to_sail.army_id,
            game_id,
            sea_path_points_for_log,
            now,
            sea_dur,
        )

        # Final Commit
        await self.session.flush()
        fleet_to_sail.task_id = resolve_army_arrival.apply_async(
            args=[fleet_to_sail.army_id], eta=arrival
        ).id
        await self.session.commit()

        # Final Response Data
        total_time_secs = sea_dur + (land_dur if needs_hybrid_journey else 0)
        direction = self.calculate_direction(start_coords, fleet_final_dest_coords)
        fog_msg = await self.get_fog_of_war_message(
            fleet_to_sail, game_id, start_coords, direction
        )
        if fleet_to_sail.troop_count <= SEA_FOG_OF_WAR_THRESHOLD:
            fog_msg = None
        return (
            True,
            {
                "image": path_data.get("image"),
                "time": format_duration(total_time_secs),
                "commander": fleet_to_sail.commander_name,
                "count": total_men_in_cargo,
                "origin": origin_name or "Sea",
                "destination": dest_name,
                "journey_summary": journey_summary,
                "gold_carried": gold_to_carry,
            },
            fog_msg,
        )

    def _debug_get_terrain_type(self, x: int, y: int) -> str:
        """
        Returns the terrain type at specific coordinates based on the Pathfinder's cost map.
        Requires COSTS to be imported from app.services.pathfinder_bot_engine.
        """
        # Ensure coordinates are within map bounds
        if not (0 <= y < self.cost_map.shape[0] and 0 <= x < self.cost_map.shape[1]):
            return "Out of Bounds"

        cost_value = self.cost_map[y, x]
        # Iterate through COSTS to find the matching name
        for name, value in COSTS.items():
            if value == cost_value:
                return name.replace("_", " ").title()  # Format nicely
        return f"Unknown Cost Value ({cost_value})"

    async def _parse_units_for_sailing(self, units_input: str):
        """Helper to parse unit strings for cargo."""
        clean_input = units_input.strip().lower()
        specific_comp, total_moving = {}, 0

        if clean_input.isdigit():
            total_moving = int(clean_input)
            specific_comp = {
                "infantry": total_moving
            }  # Assume all infantry if just a number
        else:
            parts = clean_input.replace(",", " ").split()
            for p in parts:
                if ":" in p:
                    try:
                        unit, count_str = p.split(":", 1)
                        count = int(count_str)
                        valid_units = ["infantry", "cavalry", "archers"]
                        matched_unit = next(
                            (u for u in valid_units if u.startswith(unit)), None
                        )
                        if matched_unit:
                            specific_comp[matched_unit] = count
                            total_moving += count
                    except (ValueError, IndexError):
                        continue

        if total_moving <= 0:
            raise ValueError(
                "❌ Invalid unit format. Example: `inf:500 cav:200` or `1000`"
            )
        return total_moving, specific_comp

    def _find_disembarkation_point(self, path_points):
        """
        Analyzes a path to find the last sea/port point before a land point.
        NOTE: This requires access to the cost_map from the Pathfinder engine.
            It's better to make PF_ENGINE a member of the service or pass the cost map.
            For this example, we assume self.cost_map exists.
            You would initialize it in WarfareService.__init__
        """
        sea_codes = [COSTS["ocean"], COSTS["coastal_water"], COSTS["port"]]

        # Iterate backwards to find the first land point
        for i in range(len(path_points) - 1, 0, -1):
            x, y = int(path_points[i][0]), int(path_points[i][1])

            # Boundary check
            if not (
                0 <= y < self.cost_map.shape[0] and 0 <= x < self.cost_map.shape[1]
            ):
                continue

            if self.cost_map[y, x] not in sea_codes:
                # We found the first land point. The disembark point is the one just before it.
                disembark_coords = (
                    int(path_points[i - 1][0]),
                    int(path_points[i - 1][1]),
                )
                sea_path = path_points[:i]
                return disembark_coords, sea_path

        # If no land found, the whole path is sea. Return the last point.
        return (int(path_points[-1][0]), int(path_points[-1][1])), path_points

    async def redirect_army(
        self,
        game_id: int,
        user_id: int,
        army_id: int,
        new_dest_name: str,
        new_waypoints: str | None = None,
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ):
        """
        Stops a moving army/fleet and re-issues a new move order.
        Updated to handle the 'Pending March' system and Locked ID notifications.
        """
        # 1. Standard Validation
        army_to_redirect = await ArmyRepo.get_army_by_id(self.session, army_id)
        if not army_to_redirect:
            return False, "❌ Army not found.", None

        # Determine authority context
        player = None
        if not is_gm_override:
            player = await self.session.scalar(
                select(GamePlayer).where(
                    GamePlayer.user_id == user_id, GamePlayer.game_id == game_id
                )
            )
            if not player or not player.claimed_house_id:
                return False, "❌ You do not command a house.", None

        # Effective House (The one issuing the order)
        effective_house_id = (
            acting_house_id if is_gm_override else player.claimed_house_id
        )
        if effective_house_id is None:
            effective_house_id = army_to_redirect.house_id

        # Authority check for the specific unit
        if not is_gm_override and not await self._check_command_authority(
            player, army_to_redirect
        ):
            return False, "❌ You do not have authority over this unit.", None

        if is_gm_override and army_to_redirect.house_id != effective_house_id:
            return (
                False,
                f"❌ GM override: This unit does not belong to House ID {effective_house_id}.",
                None,
            )

        # 2. Halt the Current Movement
        # stop_march handles:
        # - revoking the task
        # - calculating interpolated current position
        # - clearing 'pending_march' from cargo (if SEA)
        # - clearing MarchLogs (interception trajectories)
        stop_success, stop_msg = await self.stop_march(
            game_id, user_id, army_id, is_gm_override=is_gm_override
        )

        if not stop_success and "not moving" not in stop_msg:
            return False, stop_msg, None

        # Refresh state to ensure we have the new interpolated coordinates
        await self.session.refresh(army_to_redirect)

        # 3. Re-issue the move order based on type
        # We pass "all" units because we are redirecting the existing stack

        # --- LAND REDIRECT ---
        if army_to_redirect.army_type == "LAND":
            success, result, fog_msg = await self.march_army(
                game_id=game_id,
                user_id=user_id,
                identifier=str(army_id),
                dest_name=new_dest_name,
                units_input="all",
                commander=None,  # Keep existing
                waypoints=new_waypoints,
                gold_to_carry=0,  # Already on the army
                is_gm_override=is_gm_override,
                acting_house_id=effective_house_id,
            )
            if success:
                result["journey_summary"] = (
                    f"Orders changed: Redirected to **{new_dest_name}**."
                )
            return success, result, fog_msg

        # --- SEA REDIRECT ---
        elif army_to_redirect.army_type == "SEA":
            # Pass units_input=None to ensure it uses existing cargo
            success, result, fog_msg = await self.sail_fleet(
                game_id=game_id,
                user_id=user_id,
                fleet_id=army_id,
                ships_input="all",
                dest_name=new_dest_name,
                units_input=None,  # Uses existing cargo
                commander=None,  # Keep existing
                waypoints=new_waypoints,
                gold_to_carry=0,  # Already on the fleet
                is_gm_override=is_gm_override,
                acting_house_id=effective_house_id,
            )
            if success:
                # result['journey_summary'] is already set correctly by sail_fleet
                pass
            return success, result, fog_msg

        return False, "❌ Invalid unit type for redirection.", None

    async def occupy_fief(
        self,
        game_id: int,
        user_id: int,
        army_id: int,
        is_gm_override: bool = False,
        acting_house_id: int | None = None,
    ):
        """
        Allows an army to instantly capture a Fief if it is undefended (No Land Garrison).
        Triggers all standard Conquest logic (Loot, Asset Seizure, Garrisoning).
        """
        # 1. Load Army & Validate
        army = await ArmyRepo.get_army_by_id(self.session, army_id)
        if not army:
            return False, "❌ Army not found."

        # We need to find the GamePlayer associated with this Discord ID
        player: GamePlayer | None = None
        player_claimed_house_id: int | None = None
        if not is_gm_override:
            stmt_player = (
                select(GamePlayer)
                .join(User, GamePlayer.user_id == User.user_id)
                .where(User.discord_id == user_id, GamePlayer.game_id == game_id)
            )
            player = (await self.session.execute(stmt_player)).scalars().first()

            if not player:
                return (
                    False,
                    "❌ You do not have a registered player account in this game.",
                )
            player_claimed_house_id = player.claimed_house_id

        effective_commanding_house_id: int | None = None
        if is_gm_override:
            if acting_house_id is not None:
                effective_commanding_house_id = acting_house_id
            else:
                effective_commanding_house_id = (
                    army.house_id
                )  # Assume GM acts for the army's house
        else:
            effective_commanding_house_id = player_claimed_house_id

        if effective_commanding_house_id is None:
            return (
                False,
                "❌ Cannot determine the commanding house for this action.",
                None,
            )

        if not is_gm_override and army.house_id != player_claimed_house_id:
            return False, f"❌ Not your army. (Army House ID: {army.house_id})"

        # If GM is overriding, ensure the army belongs to the specified house
        if is_gm_override and army.house_id != effective_commanding_house_id:
            return (
                False,
                f"❌ GM override: Army {army.commander_name} does not belong to the specified acting house ID {effective_commanding_house_id}.",
                None,
            )

        if army.army_type != "LAND":
            return False, "❌ Only Land armies can occupy castles. Disembark first."

        # 2. Check Location for Fief
        stmt_fief = select(Fief).where(
            Fief.game_id == game_id,
            Fief.location_x == army.location_x,
            Fief.location_y == army.location_y,
        )
        fief = (await self.session.execute(stmt_fief)).scalars().first()

        if not fief:
            return False, "❌ There is no Fief at this location."

        if fief.owner_id == effective_commanding_house_id:
            # Just garrison if we already own it
            army.status = "GARRISONED"
            army.commander_name = f"Garrison of {fief.name}"
            await self.session.commit()
            return True, f"✅ **{army.commander_name}** has garrisoned {fief.name}."

        # 3. Check for Defenders (Land Garrison Only)
        # (Fleets cannot stop an occupation)
        stmt_def = select(Army).where(
            Army.house_id == fief.owner_id,
            Army.location_x == fief.location_x,
            Army.location_y == fief.location_y,
            Army.status == "GARRISONED",
            Army.army_type == "LAND",
        )
        defender = (await self.session.execute(stmt_def)).scalars().first()

        if defender:
            return (
                False,
                f'❌ **{fief.name} is defended!**\nYou cannot simply walk in. You must use `!siege {army_id} "{fief.name}"`.',
            )

        # 4. EXECUTE CONQUEST (Same logic as Siege Win)
        victim_house = await self.session.get(House, fief.owner_id)
        attacker_house = await self.session.get(House, effective_commanding_house_id)

        # Loot
        loot = victim_house.treasury if victim_house else 0
        if attacker_house:
            attacker_house.treasury += loot
        if victim_house:
            victim_house.treasury = 0

        # Transfer Fief
        fief.owner_id = effective_commanding_house_id
        fief.integration = 0.10

        # Garrison Attacker
        army.status = "GARRISONED"
        army.commander_name = f"Garrison of {fief.name}"

        # 5. Asset Seizure (Capture Fleets/Idle Armies)
        stmt_assets = select(Army).where(
            Army.house_id == victim_house.house_id if victim_house else -1,
            Army.location_x == fief.location_x,
            Army.location_y == fief.location_y,
            Army.army_id != army.army_id,  # Don't capture yourself
        )
        assets = (await self.session.execute(stmt_assets)).scalars().all()

        captured_text = ""
        for asset in assets:
            asset.house_id = effective_commanding_house_id
            if asset.army_type == "SEA":
                asset.status = "DOCKED"
                asset.commander_name = f"Captured Fleet ({asset.troop_count})"
                captured_text += f"\n⚓ **Captured Fleet:** {asset.troop_count} Ships"
            else:
                asset.status = "GARRISONED"
                asset.commander_name = f"Captured Garrison ({asset.troop_count})"
                captured_text += f"\n🏳️ **Captured Army:** {asset.troop_count} Troops"

        await self.session.commit()

        return True, (
            f"🏰 **{fief.name} Occupied!**\n"
            f"Since there was no garrison, your forces marched right in.\n"
            f"💰 Treasury seized.\n"
            f"📉 Integration reset to **10%**.{captured_text}"
        )

    async def delete_army(self, game_id: int, army_id: int):
        """
        GM Tool: Forcefully deletes an army and cleans up all references
        (Interactions, Battles, March Logs, Celery Tasks).
        """
        army = await self.session.get(Army, army_id)
        if not army:
            return False, "❌ Army not found."

        # 1. Stop Movement (Revoke Celery Task)
        if army.task_id:
            try:
                AsyncResult(army.task_id, app=celery_app).revoke(terminate=True)
            except Exception as e:
                print(f"[WARN] Failed to revoke task for army {army_id}: {e}")

        # 2. Delete Pending Interactions (Prevents FK Error)
        await self.session.execute(
            delete(PendingInteraction).where(
                or_(
                    PendingInteraction.army1_id == army_id,
                    PendingInteraction.army2_id == army_id,
                )
            )
        )

        # 3. Delete Active Battles (Prevents FK Error)
        await self.session.execute(
            delete(Battle).where(
                or_(Battle.attacker_id == army_id, Battle.defender_id == army_id)
            )
        )

        # 4. Delete March Logs (The Fix for your current error)
        await self.session.execute(delete(MarchLog).where(MarchLog.army_id == army_id))

        # 5. Capture Info for Log
        name = army.commander_name
        troops = army.troop_count
        gold = army.treasury or 0
        cargo_info = ""
        if army.cargo:
            c_count = army.cargo.get("troop_count", 0)
            if c_count > 0:
                cargo_info = f" (carrying {c_count} troops)"

        # 6. Delete the Army
        await self.session.delete(army)
        await self.session.commit()

        return (
            True,
            f"✅ **Deleted:** {name} (ID: {army_id})\n📉 **Loss:** {troops} units{cargo_info} and {gold} gold.",
        )
