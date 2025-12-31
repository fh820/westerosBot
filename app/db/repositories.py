from sqlalchemy import select, delete, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Game, House, User, GamePlayer, Army, Fief, MarchLog
import datetime
import copy
from app.db.models import Fief
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified


class GameRepo:
    @staticmethod
    async def create_game(session: AsyncSession, guild_id: int, name: str = "Westeros"):
        game = Game(guild_id=guild_id, name=name, is_active=True)
        session.add(game)
        await session.flush()
        return game

    @staticmethod
    async def get_active_game(session: AsyncSession, guild_id: int):
        stmt = select(Game).where(Game.guild_id == guild_id, Game.is_active == True)
        result = await session.execute(stmt)
        return result.scalars().first()


class HouseRepo:
    @staticmethod
    async def create_house(session: AsyncSession, game_id: int, data: dict):
        house = House(
            game_id=game_id,
            name=data.get("house", "Unknown"),
            region=data.get("region", "Unknown"),
            castle=data.get("castle", "Unknown"),
            color_hex=data.get("color", "#FFFFFF"),
        )
        session.add(house)
        await session.flush()
        return house

    @staticmethod
    async def get_house_by_name(session: AsyncSession, game_id: int, name: str):
        stmt = select(House).where(House.game_id == game_id, House.name.ilike(name))
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_house_by_name_or_id(
        session: Session, identifier: str
    ) -> House | None:
        try:
            # Check if identifier is an ID
            house_id = int(identifier)
            return await session.get(House, house_id)
        except ValueError:
            # If not an ID, assume it's a name
            return await session.scalar(
                select(House).where(House.name.ilike(identifier))
            )


class ArmyRepo:
    @staticmethod
    async def get_army_by_id(session, army_id: int):
        stmt = select(Army).where(Army.army_id == army_id)
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def log_march_path(
        session,
        army_id: int,
        game_id: int,
        path_points: list,
        start_time: datetime.datetime,
        duration_seconds: int,
    ):
        await session.execute(delete(MarchLog).where(MarchLog.army_id == army_id))

        total_points = len(path_points)
        if total_points < 2:
            return

        step = 10
        logs = []
        for i in range(0, total_points, step):
            progress_pct = i / total_points
            time_offset = duration_seconds * progress_pct
            timestamp = start_time + datetime.timedelta(seconds=time_offset)

            logs.append(
                MarchLog(
                    game_id=game_id,
                    army_id=army_id,
                    x=float(path_points[i][0]),
                    y=float(path_points[i][1]),
                    estimated_time=timestamp,
                )
            )

        session.add_all(logs)
        await session.flush()

    @staticmethod
    async def check_trajectory_collision(
        session,
        game_id: int,
        army_id: int,
        x: float,
        y: float,
        time_at_point: datetime.datetime,
    ):
        time_window = datetime.timedelta(minutes=45)
        start_window = time_at_point - time_window
        end_window = time_at_point + time_window
        radius = 75.0

        stmt = select(MarchLog.army_id).where(
            MarchLog.game_id == game_id,
            MarchLog.army_id != army_id,
            MarchLog.estimated_time.between(start_window, end_window),
            MarchLog.x.between(x - radius, x + radius),
            MarchLog.y.between(y - radius, y + radius),
        )

        results = (await session.execute(stmt)).scalars().all()
        return list(set(results))

    @staticmethod
    async def clear_march_logs(session, army_id: int):
        await session.execute(delete(MarchLog).where(MarchLog.army_id == army_id))

    @staticmethod
    def _calculate_split(original_comp: dict, split_amount: int, total_troops: int):
        if total_troops <= 0:
            return {}, original_comp
        ratio = split_amount / total_troops
        new_comp = {}
        source_comp = copy.deepcopy(original_comp)
        assigned = 0
        for unit, count in source_comp.items():
            moving = int(count * ratio)
            new_comp[unit] = moving
            source_comp[unit] -= moving
            assigned += moving
        remainder = split_amount - assigned
        if remainder > 0:
            primary = (
                max(new_comp, key=new_comp.get)
                if new_comp
                else list(source_comp.keys())[0]
            )
            new_comp[primary] += remainder
            source_comp[primary] -= remainder
        return new_comp, source_comp

    @staticmethod
    async def create_marching_army_specific(
        session,
        original_army: Army,
        composition_to_move: dict,
        commander_name: str,
    ):
        """
        Creates a new land army with a specific composition by splitting a source army.
        This simplified version no longer needs destination or duration.
        """
        source_comp = dict(original_army.composition)
        total_moving = 0
        for unit, amount in composition_to_move.items():
            if amount <= 0:
                continue
            if source_comp.get(unit, 0) < amount:
                raise ValueError(f"Not enough {unit}.")
            source_comp[unit] -= amount
            total_moving += amount

        if total_moving <= 0:
            raise ValueError("Cannot create an army with zero troops.")

        original_army.composition = source_comp
        original_army.troop_count -= total_moving

        new_army = Army(
            game_id=original_army.game_id,
            house_id=original_army.house_id,
            army_type="LAND",
            commander_name=commander_name,
            troop_count=total_moving,
            composition=composition_to_move,
            location_x=original_army.location_x,
            location_y=original_army.location_y,
            status="IDLE",  # The service will set its status to MARCHING
        )
        session.add(new_army)
        await session.flush()
        return new_army

    @staticmethod
    async def create_marching_army(
        session,
        original_army: Army,
        troops_to_move: int,
        commander_name: str,
    ):
        """
        Creates a new land army by splitting a source army.
        This simplified version no longer needs destination or duration.
        """
        new_comp, updated_source = ArmyRepo._calculate_split(
            original_army.composition, troops_to_move, original_army.troop_count
        )
        original_army.composition = updated_source
        original_army.troop_count -= troops_to_move

        new_army = Army(
            game_id=original_army.game_id,
            house_id=original_army.house_id,
            army_type="LAND",
            commander_name=commander_name,
            troop_count=troops_to_move,
            composition=new_comp,
            location_x=original_army.location_x,
            location_y=original_army.location_y,
            status="IDLE",  # The service will set its status to MARCHING
        )
        session.add(new_army)
        await session.flush()
        return new_army

    @staticmethod
    async def muster_from_garrison(
        session: AsyncSession,
        game_id: int,
        # --- SIGNATURE CHANGE ---
        # owner_house_id: The liege who will own the new army.
        # source_house_id: The vassal providing the troops.
        owner_house_id: int,
        source_house_id: int,
        # --- END CHANGE ---
        troops_to_muster: int,
        commander_name: str,
    ) -> Army | None:
        """
        Finds the garrison at a vassal's main fief, musters troops from it,
        and creates a new field army under the liege's control.
        """
        # 1. Find the SOURCE house's main fief to locate the garrison
        fief = await FiefRepo.get_main_fief_for_house(session, source_house_id)
        if not fief:
            return None  # Vassal has no land to muster from

        # 2. Find the garrison army at that location belonging to the SOURCE house
        stmt = select(Army).where(
            Army.game_id == game_id,
            # --- LOGIC CHANGE ---
            Army.house_id == source_house_id,  # Find the VASSAL'S garrison
            # --- END CHANGE ---
            Army.location_x == fief.location_x,
            Army.location_y == fief.location_y,
            Army.status == "GARRISONED",
            Army.army_type == "LAND",
        )
        garrison = (await session.execute(stmt)).scalars().first()

        if not garrison or garrison.troop_count < troops_to_muster:
            return None  # No garrison or not enough troops

        # 3. Use the existing split logic to create the new army's composition
        new_comp, updated_garrison_comp = ArmyRepo._calculate_split(
            garrison.composition, troops_to_muster, garrison.troop_count
        )

        # 4. Update the garrison's numbers
        garrison.composition = updated_garrison_comp
        garrison.troop_count -= troops_to_muster
        flag_modified(garrison, "composition")  # Important for JSON fields

        # 5. Create the new field army
        new_army = Army(
            game_id=garrison.game_id,
            # --- LOGIC CHANGE ---
            house_id=owner_house_id,  # Assign the new army to the LIEGE
            # --- END CHANGE ---
            army_type="LAND",
            commander_name=commander_name,
            troop_count=troops_to_muster,
            composition=new_comp,
            location_x=garrison.location_x,
            location_y=garrison.location_y,
            status="IDLE",  # The command will set this to MARCHING
        )
        session.add(new_army)
        await session.flush()  # So the new_army gets an ID
        return new_army

    @staticmethod
    async def muster_fleet_from_garrison(
        session,
        game_id: int,
        liege_house_id: int,
        vassal_house_id: int,
        ships_to_muster: int,
        commander_name: str,
    ) -> Army | None:
        """
        Finds the largest garrisoned fleet for a vassal, reduces its ship count,
        and creates a new fleet levy under the liege's control.
        """
        if ships_to_muster <= 0:
            return None

        # Find the source fleet to take ships from
        stmt = (
            select(Army)
            .where(
                Army.house_id == vassal_house_id,
                Army.army_type == "SEA",
                or_(Army.status == "GARRISONED", Army.status == "IDLE"),
            )
            .order_by(Army.troop_count.desc())
            .limit(1)
        )
        source_fleet = (await session.execute(stmt)).scalars().first()

        if not source_fleet or source_fleet.troop_count < ships_to_muster:
            return None  # Not enough ships to fulfill the levy

        # Reduce the vassal's fleet size
        source_fleet.troop_count -= ships_to_muster

        # Create the new levy fleet for the liege
        new_levy_fleet = Army(
            game_id=game_id,
            house_id=liege_house_id,
            commander_name=commander_name,
            troop_count=ships_to_muster,
            composition={},  # Fleets often don't have composition in the same way
            location_x=source_fleet.location_x,
            location_y=source_fleet.location_y,
            status="IDLE",
            army_type="SEA",
        )
        session.add(new_levy_fleet)
        await session.flush()  # Ensure the new fleet gets an ID
        return new_levy_fleet

    @staticmethod
    async def split_army_logic(
        session, source_army: Army, split_amount: int, new_commander_name: str
    ):
        """
        Splits army in place. Handles Cargo splitting for Fleets.
        """
        # 1. Calculate Composition Split
        new_comp_dict, updated_source_dict = ArmyRepo._calculate_split(
            source_army.composition, split_amount, source_army.troop_count
        )

        # 2. Handle Cargo (If Fleet)
        new_cargo = None
        if source_army.cargo:
            # Calculate ratio of ships being moved
            # split_amount = number of ships moving
            # source_army.troop_count = total ships
            ratio = split_amount / source_army.troop_count

            # Calculate cargo to move
            cargo_troops = source_army.cargo.get("troop_count", 0)
            moving_cargo_count = int(cargo_troops * ratio)

            if moving_cargo_count > 0:
                # Split the cargo composition
                cargo_comp = source_army.cargo.get("composition", {})
                move_c_comp, keep_c_comp = ArmyRepo._calculate_split(
                    cargo_comp, moving_cargo_count, cargo_troops
                )

                # Create new cargo dict
                new_cargo = {
                    "commander": f"Garrison of {new_commander_name}",
                    "troop_count": moving_cargo_count,
                    "composition": move_c_comp,
                }

                # Update old cargo dict
                source_army.cargo = {
                    "commander": source_army.cargo.get("commander"),
                    "troop_count": cargo_troops - moving_cargo_count,
                    "composition": keep_c_comp,
                }
                # If old fleet is empty of cargo, set to None? No, keep empty dict structure or just None if count is 0
                if source_army.cargo["troop_count"] <= 0:
                    source_army.cargo = None

        # 3. Update Source
        source_army.composition = updated_source_dict
        source_army.troop_count -= split_amount

        # 4. Create New
        new_army = Army(
            game_id=source_army.game_id,
            house_id=source_army.house_id,
            army_type=source_army.army_type,  # Copy type (LAND/SEA)
            commander_name=new_commander_name,
            troop_count=split_amount,
            composition=new_comp_dict,
            location_x=source_army.location_x,
            location_y=source_army.location_y,
            status="IDLE",
            cargo=new_cargo,  # Assign the split cargo
        )
        session.add(new_army)
        await session.flush()
        return new_army

    @staticmethod
    async def get_armies_by_ids(
        session: AsyncSession, army_ids: list[int]
    ) -> list[Army]:
        """
        Efficiently fetches multiple Army objects from a list of IDs.
        """
        if not army_ids:
            return []

        stmt = select(Army).where(Army.army_id.in_(army_ids))
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def merge_army_logic(session, source_army: Army, target_army: Army):
        """
        Merges source_army INTO target_army.
        Handles both regular troops and FLEET CARGO merging safely.
        Deletes source_army after merge.
        """
        # 1. Merge Main Composition (Ships or Men)
        target_comp = dict(target_army.composition or {})
        source_comp = dict(source_army.composition or {})

        for unit, count in source_comp.items():
            target_comp[unit] = target_comp.get(unit, 0) + count

        target_army.composition = target_comp
        target_army.troop_count += source_army.troop_count

        # 2. Merge Cargo (Only if both are Fleets)
        # We must verify they are fleets to avoid errors with non-existent cargo columns
        if source_army.army_type == "SEA" and target_army.army_type == "SEA":

            # Get Cargo Data safely (handle None)
            s_cargo = source_army.cargo or {"troop_count": 0, "composition": {}}
            t_cargo = target_army.cargo or {"troop_count": 0, "composition": {}}

            # Calculate New Total Cargo Count
            new_cargo_count = t_cargo.get("troop_count", 0) + s_cargo.get(
                "troop_count", 0
            )

            # Merge Cargo Compositions
            t_cargo_comp = dict(t_cargo.get("composition", {}) or {})
            s_cargo_comp = dict(s_cargo.get("composition", {}) or {})

            for unit, count in s_cargo_comp.items():
                t_cargo_comp[unit] = t_cargo_comp.get(unit, 0) + count

            # Update the Target Fleet's Cargo
            if new_cargo_count > 0:
                target_army.cargo = {
                    "commander": t_cargo.get("commander")
                    or f"Garrison of {target_army.commander_name}",
                    "troop_count": new_cargo_count,
                    "composition": t_cargo_comp,
                }
            else:
                # If both were empty, ensure it remains None or empty dict
                target_army.cargo = None

        # 3. Delete the Source Army
        await session.delete(source_army)

        # 4. Flush changes to prepare for commit
        await session.flush()

        return target_army

    @staticmethod
    async def create_embarked_army(
        session: AsyncSession,
        fleet: Army,
        dest_x: int,
        dest_y: int,
        departure_time: datetime,
    ) -> Army:
        """Creates a new land army from a fleet's cargo, ready for marching."""
        cargo = fleet.cargo
        new_army = Army(
            game_id=fleet.game_id,
            house_id=fleet.house_id,
            army_type="LAND",
            commander_name=cargo.get("commander", "Disembarked Host"),
            troop_count=cargo.get("troop_count", 0),
            composition=cargo.get("composition", {}),
            location_x=fleet.destination_x,  # Starts where the fleet ends
            location_y=fleet.destination_y,
            destination_x=dest_x,
            destination_y=dest_y,
            status="MARCHING",  # It will start marching immediately upon creation
            departure_time=departure_time,
        )
        session.add(new_army)

        # Clear the fleet's cargo as it's now a separate entity
        fleet.cargo = None

        return new_army

    @staticmethod
    async def create_sailing_fleet(
        session: AsyncSession,
        source_fleet: Army,
        ship_count: int,
        commander_name: str,
        cargo: dict = None,  # <--- Ensure this argument exists
    ) -> Army:
        """
        Creates a new fleet, injecting cargo IMMEDIATELY into the INSERT statement.
        """
        if ship_count >= source_fleet.troop_count:
            raise ValueError("Ship count must be less than the source fleet's total.")

        source_fleet.troop_count -= ship_count

        # 1. Prepare Cargo (Ensure it is a valid dictionary, never None)
        final_cargo = cargo if cargo else {}

        # 2. Create the Fleet with Cargo PRE-LOADED
        new_fleet = Army(
            game_id=source_fleet.game_id,
            house_id=source_fleet.house_id,
            army_type="SEA",
            commander_name=commander_name,
            troop_count=ship_count,
            composition={"ships": ship_count},
            location_x=source_fleet.location_x,
            location_y=source_fleet.location_y,
            status="IDLE",
            cargo=final_cargo,  # <--- CRITICAL: Saves data in the first SQL command
            treasury=0,
        )

        session.add(new_fleet)
        await session.flush()
        return new_fleet


class FiefRepo:
    @staticmethod
    async def get_all_fief_names(session: Session, game_id: int) -> list[str]:
        """Returns a sorted list of all fief names for the given game."""
        stmt = select(Fief.name).where(Fief.game_id == game_id).order_by(Fief.name)
        result = await session.execute(stmt)
        return result.scalars().all()

    @classmethod
    async def get_by_name(
        cls, session: AsyncSession, game_id: int, fief_name: str
    ) -> Fief | None:
        """
        Finds a single fief by its name (case-insensitive).
        """
        stmt = select(Fief).where(
            Fief.game_id == game_id,
            Fief.name.ilike(fief_name),  # ilike for case-insensitive matching
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    async def get_main_fief_for_house(
        cls, session: AsyncSession, house_id: int
    ) -> Fief | None:
        """
        Finds the primary fief (capital) for a given house.
        This is a simple implementation that just returns the first fief found.
        """
        stmt = select(Fief).where(Fief.owner_id == house_id).limit(1)
        result = await session.execute(stmt)
        return result.scalars().first()
