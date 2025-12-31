import random
from sqlalchemy import select, delete, text
from sqlalchemy.orm import selectinload, Session
from app.db.repositories import ArmyRepo
from app.db.models import Army, Character, House, Battle, Fief
from app.services.engine_manager import PF_ENGINE
from app.db.models import GamePlayer, User

# Used by the async method for GM-driven narration
from app.services.chronicler import generate_battle_narration
from sqlalchemy.orm.attributes import flag_modified

# --- CONFIGURATION ---
UNIT_STATS = {
    "knights": {"value": 15.0},
    "cavalry": {"value": 5.0},
    "infantry": {"value": 3.5},
    "archers": {"value": 2.5},
    "militia": {"value": 1.0},
    "warships": {"value": 100.0},
}
WINNER_CASUALTY_TABLE = {0: 0.05, 1: 0.10, 2: 0.15, 3: 0.20, 4: 0.25, 5: 0.30}
LOSER_CASUALTY_TABLE = {0: 0.45, 1: 0.50, 2: 0.55, 3: 0.60, 4: 0.65, 5: 0.70}


class BattleService:
    def __init__(self, session: Session):
        self.session = session

    # --- SHARED HELPER ---
    def _calculate_army_bp(self, army):
        if not army or not army.composition:
            return 0, 0
        total_value = sum(
            count * UNIT_STATS.get(unit.lower(), {}).get("value", 0)
            for unit, count in army.composition.items()
        )
        return total_value, total_value / 250.0

    # --- NEW HELPER: STOP MOVEMENT (PREVENTS RACE CONDITIONS) ---
    def _stop_movement_immediately(self, army):
        """
        Revokes the arrival task and clears movement data.
        Used when a battle STARTS to prevent the army from 'arriving' while fighting.
        """
        if not army:
            return

        # 1. Revoke Celery Task
        if army.task_id:
            from app.celery_app import celery_app

            try:
                celery_app.control.revoke(army.task_id, terminate=True)
            except Exception as e:
                print(f"[BATTLE] Failed to revoke task: {e}")
            army.task_id = None

        # 2. Clear Pathing Data
        army.destination_x = None
        army.destination_y = None
        army.arrival_time = None
        army.departure_time = None

    # ====================================================================
    # ===== ASYNCHRONOUS METHODS (For GM Commands) =======================
    # ====================================================================

    async def _get_character_martial(self, house_id: int, commander_name: str) -> int:
        if not commander_name:
            return 0
        stmt = select(Character).where(
            Character.house_id == house_id, Character.name.ilike(commander_name)
        )
        char = (await self.session.execute(stmt)).scalars().first()
        return char.skills.get("martial", 0) if char and char.skills else 0

    async def _get_valid_retreat_fief(self, house_id: int, army: Army) -> Fief | None:
        """Finds nearest valid retreat fief."""
        stmt = select(Fief).where(Fief.owner_id == house_id)
        fiefs = (await self.session.execute(stmt)).scalars().all()

        if not fiefs:
            return None

        pf = PF_ENGINE
        start_pos = {"x": army.location_x, "y": army.location_y}
        strict_mode = "land_only" if army.army_type == "LAND" else "sea_only"

        # Sort by distance
        fiefs.sort(
            key=lambda f: (f.location_x - start_pos["x"]) ** 2
            + (f.location_y - start_pos["y"]) ** 2
        )

        for fief in fiefs:
            end_pos = {"x": fief.location_x, "y": fief.location_y}
            dist_sq = (start_pos["x"] - end_pos["x"]) ** 2 + (
                start_pos["y"] - end_pos["y"]
            ) ** 2

            if dist_sq < 1.0:
                return fief

            # Attempt 1: Strict
            path_data = await pf.find_journey_async(
                start_pos, end_pos, travel_mode=strict_mode, gm_settings={}
            )

            # Attempt 2: Optimal (Fallback)
            if not path_data and strict_mode == "land_only":
                path_data = await pf.find_journey_async(
                    start_pos, end_pos, travel_mode="optimal", gm_settings={}
                )

            if path_data:
                return fief

            # Attempt 3: Close proximity force
            if dist_sq < 90000:
                return fief

        return None

    # ====================================================================
    # ===== BATTLE LOGIC (UPDATED WITH STOP MOVEMENT) ====================
    # ====================================================================

    async def start_battle(self, game_id, attacker_id, defender_id, ambush, defense):
        stmt = (
            select(Army)
            .where(Army.army_id.in_([attacker_id, defender_id]))
            .options(selectinload(Army.house))
        )
        armies = (await self.session.execute(stmt)).scalars().all()
        attacker = next((a for a in armies if a.army_id == attacker_id), None)
        defender = next((a for a in armies if a.army_id == defender_id), None)

        if not attacker or not defender:
            return None, "❌ Armies not found.", None
        if attacker.army_type != defender.army_type:
            return None, "❌ Cannot mix land and sea.", None

        battle_type = "SEA_BATTLE" if attacker.army_type == "SEA" else "LAND_BATTLE"
        _, att_bp = self._calculate_army_bp(attacker)
        _, def_bp = self._calculate_army_bp(defender)
        att_martial = await self._get_character_martial(
            attacker.house_id, attacker.commander_name
        )
        def_martial = await self._get_character_martial(
            defender.house_id, defender.commander_name
        )

        att_bonus, def_bonus = att_martial, def_martial
        if battle_type == "LAND_BATTLE":
            att_bonus += {"extreme": 15, "good": 10, "decent": 5, "failed": -5}.get(
                ambush.lower(), 0
            )
            def_bonus += {"major": 20, "significant": 10, "minor": 5}.get(
                defense.lower(), 0
            )

        if attacker.troop_count > defender.troop_count * 1.2:
            att_bonus += 4
        elif defender.troop_count > attacker.troop_count * 1.2:
            def_bonus += 4

        odds = int(max(1, min(99, 50 + ((att_bp + att_bonus) - (def_bp + def_bonus)))))

        new_battle = Battle(
            game_id=game_id,
            attacker_id=attacker.army_id,
            defender_id=defender.army_id,
            current_odds=odds,
            battle_type=battle_type,
        )
        self.session.add(new_battle)

        # --- STOP MOVEMENT NOW ---
        self._stop_movement_immediately(attacker)
        self._stop_movement_immediately(defender)

        await self.session.commit()
        calc_log = f"**Attacker:** Units `{att_bp:.1f}` + Bonus `{att_bonus}`\n**Defender:** Units `{def_bp:.1f}` + Bonus `{def_bonus}`"
        return new_battle, f"Attacker Odds: 1 - {odds}", calc_log

    async def process_battle_round(self, battle_id: int):
        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(
                selectinload(Battle.attacker).selectinload(Army.house),
                selectinload(Battle.defender).selectinload(Army.house),
                selectinload(Battle.game),
                selectinload(Battle.fief),
            )
        )
        battle = (await self.session.execute(stmt)).scalars().first()
        if not battle:
            return None, "Battle not found.", None, False, None, None

        # --- CHECK IF BATTLE IS ALREADY OVER ---
        if battle.attacker_score >= 5 or battle.defender_score >= 5:
            is_siege_transition = (
                battle.battle_type == "SIEGE"
                and battle.siege_phase == "WALLS"
                and battle.attacker_score >= 5
            )
            if not is_siege_transition:
                winner = "Attacker" if battle.attacker_score >= 5 else "Defender"
                return (
                    battle,
                    "Battle already finished.",
                    winner,
                    False,
                    "Battle is over.",
                    {},
                )

        # --- RESOLVE ROLL ---
        roll = random.randint(1, 100)
        is_attacker_win = roll <= battle.current_odds

        if is_attacker_win:
            battle.attacker_score += 1
        else:
            battle.defender_score += 1

        # --- MOMENTUM / PHASE LOGIC ---
        phase_transition = False
        if battle.battle_type == "SIEGE":
            if battle.siege_phase == "WALLS" and battle.attacker_score >= 5:
                phase_transition = True
                battle.siege_phase = "STREETS"
                battle.attacker_score, battle.defender_score = 0, 0
                battle.current_odds = 75
            elif battle.siege_phase == "STREETS":
                if is_attacker_win:
                    battle.current_odds = min(95, battle.current_odds + 5)
                else:
                    battle.current_odds = max(5, battle.current_odds - 5)
        else:
            if is_attacker_win:
                battle.current_odds = min(95, battle.current_odds + 5)
            else:
                battle.current_odds = max(5, battle.current_odds - 5)

        # --- CASUALTY CALCULATION ---
        att_loss_pct = random.uniform(0.01, 0.03)
        def_loss_pct = random.uniform(0.01, 0.03)
        if is_attacker_win:
            def_loss_pct += 0.02
        else:
            att_loss_pct += 0.02

        # 1. Calculate Ship/Troop Losses
        att_losses = int(battle.attacker.troop_count * att_loss_pct)
        def_losses = int(battle.defender.troop_count * def_loss_pct)

        # 2. Apply Ship/Troop Losses
        battle.attacker.troop_count = max(0, battle.attacker.troop_count - att_losses)
        battle.defender.troop_count = max(0, battle.defender.troop_count - def_losses)

        # --- FIX: CARGO ATTRITION (Drowning) ---
        # If ships sank, we must reduce the cargo proportionally NOW.
        from sqlalchemy.orm.attributes import flag_modified

        def apply_cargo_damage(army, loss_pct):
            if (
                army.army_type == "SEA"
                and army.cargo
                and army.cargo.get("troop_count", 0) > 0
            ):
                c_men = army.cargo["troop_count"]

                # Men die at the same rate as ships
                c_losses = int(c_men * loss_pct)
                c_new = max(0, c_men - c_losses)

                # Recalculate cargo composition
                if c_losses > 0:
                    c_comp, _ = ArmyRepo._calculate_split(
                        army.cargo.get("composition", {}), c_new, c_men
                    )
                    # Trigger SQL Update explicitly
                    new_cargo = dict(army.cargo)
                    new_cargo["troop_count"] = c_new
                    new_cargo["composition"] = c_comp
                    army.cargo = new_cargo
                    flag_modified(army, "cargo")

        apply_cargo_damage(battle.attacker, att_loss_pct)
        apply_cargo_damage(battle.defender, def_loss_pct)
        # ---------------------------------------

        # --- NARRATION ---
        round_winner_name = (
            battle.attacker.commander_name
            if is_attacker_win
            else battle.defender.commander_name
        )
        narration = f"The forces of **{round_winner_name}** push forward!"

        try:
            ai_narration = await generate_battle_narration(
                battle, roll, is_attacker_win, att_losses, def_losses
            )
            if ai_narration:
                narration = ai_narration
        except Exception:
            pass

        await self.session.commit()

        # --- WINNER CHECK ---
        winner = None
        if battle.defender_score >= 5:
            winner = "Defender"
        elif battle.attacker_score >= 5:
            if battle.battle_type == "SIEGE":
                if battle.siege_phase == "STREETS":
                    winner = "Attacker"
            else:
                winner = "Attacker"

        return (
            battle,
            f"Roll: **{roll}**.",
            winner,
            phase_transition,
            narration,
            {"attacker": att_losses, "defender": def_losses},
        )

    # async def resolve_manual_battle_aftermath(self, battle_id: int) -> str:
    #     """
    #     Resolves the aftermath for a GM-DRIVEN battle.
    #     UPDATED:
    #     - Disables double-dipping casualties (Manual rounds already applied damage).
    #     - Fixed report wording to avoid "0 Casualties" confusion.
    #     - Keeps Cargo/Ghost Army cleanup logic.
    #     """
    #     stmt = (
    #         select(Battle)
    #         .where(Battle.id == battle_id)
    #         .options(
    #             selectinload(Battle.attacker)
    #             .selectinload(Army.house)
    #             .selectinload(House.game),
    #             selectinload(Battle.defender).selectinload(Army.house),
    #         )
    #     )
    #     battle = (await self.session.execute(stmt)).scalars().first()
    #     if not battle:
    #         return "Error: Battle not found."

    #     attacker, defender = battle.attacker, battle.defender
    #     ship_capacity = battle.game.ship_capacity

    #     # --- STEP 1: SNAPSHOT DATA ---
    #     def get_snapshot(army):
    #         if not army:
    #             return {
    #                 "exists": False,
    #                 "count": 0,
    #                 "cargo_count": 0,
    #                 "comp": {},
    #                 "name": "Unknown",
    #                 "house": "Unknown",
    #             }
    #         return {
    #             "exists": True,
    #             "count": army.troop_count,
    #             "cargo_count": army.cargo.get("troop_count", 0) if army.cargo else 0,
    #             "comp": dict(army.composition or {}),
    #             "name": army.commander_name,
    #             "house": army.house.name if army.house else "Unknown",
    #         }

    #     att_snap = get_snapshot(attacker)
    #     def_snap = get_snapshot(defender)

    #     if battle.attacker_score >= battle.defender_score:
    #         winner, loser = attacker, defender
    #     else:
    #         winner, loser = defender, attacker

    #     loser_id_to_delete = None

    #     # --- STEP 2: CARGO CAPACITY CHECK ONLY (No Extra Casualties) ---
    #     from sqlalchemy.orm.attributes import flag_modified

    #     # We DO NOT run 'apply_losses' here for Manual Battles because damage
    #     # was already applied round-by-round. We only enforce cargo physics.
    #     def enforce_cargo_limits(army):
    #         if army and army.army_type == "SEA" and army.cargo:
    #             max_cap = army.troop_count * ship_capacity
    #             current = army.cargo.get("troop_count", 0)

    #             # If ships sank during the battle, force cargo to drop now
    #             if current > max_cap:
    #                 new_count = max_cap
    #                 new_comp, _ = ArmyRepo._calculate_split(
    #                     army.cargo.get("composition", {}), new_count, current
    #                 )
    #                 nc = dict(army.cargo)
    #                 nc["troop_count"] = new_count
    #                 nc["composition"] = new_comp
    #                 army.cargo = nc
    #                 flag_modified(army, "cargo")

    #     enforce_cargo_limits(winner)
    #     enforce_cargo_limits(loser)

    #     # --- STEP 3: STOP MOVEMENT TASKS ---
    #     from app.celery_app import celery_app

    #     def kill_task(army_obj):
    #         if not army_obj or not army_obj.task_id:
    #             return
    #         try:
    #             celery_app.control.revoke(army_obj.task_id, terminate=True)
    #         except:
    #             pass
    #         army_obj.task_id = None
    #         army_obj.destination_x = None
    #         army_obj.destination_y = None
    #         army_obj.arrival_time = None
    #         army_obj.departure_time = None

    #     kill_task(loser)
    #     kill_task(winner)

    #     # --- STEP 4: RETREAT / CAPTURE ---
    #     commander_fate_str = "The losing army was completely wiped out."
    #     should_delete_loser = False

    #     loser_mention = f"**{loser.house.name}**" if loser and loser.house else "Enemy"
    #     if loser and loser.house_id:
    #         stmt_user = (
    #             select(User.discord_id)
    #             .join(GamePlayer, GamePlayer.user_id == User.user_id)
    #             .where(
    #                 GamePlayer.game_id == battle.game_id,
    #                 GamePlayer.claimed_house_id == loser.house_id,
    #             )
    #         )
    #         discord_id = (await self.session.execute(stmt_user)).scalar()
    #         if discord_id:
    #             loser_mention = f"<@{discord_id}>"

    #     if loser and loser.troop_count > 0:
    #         loser_martial = await self._get_character_martial(
    #             loser.house_id, loser.commander_name
    #         )
    #         retreat_odds = 40 + (loser_martial * 2)

    #         if random.randint(1, 100) <= retreat_odds:
    #             loser.status = "RETREATING"
    #             loser.destination_x = None
    #             loser.destination_y = None
    #             loser.arrival_time = None
    #             unit_term = "ships" if loser.army_type == "SEA" else "troops"
    #             commander_fate_str = f"**{loser.commander_name}** managed to disengage! The remaining {loser.troop_count} {unit_term} have scattered (Status: **RETREATING**).\n⚠️ {loser_mention} **ACTION REQUIRED:** You must manually move them to safety."
    #         else:
    #             # Rout
    #             sp = 0
    #             if loser.army_type == "SEA" and loser.cargo:
    #                 sp = loser.cargo.get("troop_count", 0)
    #             else:
    #                 sp = loser.troop_count

    #             if sp > 0 and winner:
    #                 if not winner.cargo:
    #                     winner.cargo = {}
    #                 if "prisoners" not in winner.cargo:
    #                     winner.cargo["prisoners"] = []
    #                 winner.cargo["prisoners"].append(
    #                     {"house": loser.house.name, "count": sp}
    #                 )
    #                 flag_modified(winner, "cargo")

    #             fate_str = (
    #                 "Captured" if random.randint(1, 100) <= 50 else "Killed in Action"
    #             )
    #             commander_fate_str = f"The retreat failed! **{loser.commander_name}** was **{fate_str}**! {loser_mention}, your forces were lost."
    #             should_delete_loser = True
    #     elif loser:
    #         commander_fate_str = f"The force was completely destroyed! **{loser.commander_name}** was **Killed in Action**!"
    #         should_delete_loser = True

    #     # Loot
    #     loot = loser.treasury if loser else 0
    #     if winner and winner.house:
    #         winner_house = await self.session.get(House, winner.house_id)
    #         if winner_house:
    #             winner_house.treasury += loot
    #     if loser:
    #         loser.treasury = 0

    #     if loser and loser.troop_count <= 0:
    #         should_delete_loser = True
    #     if should_delete_loser and loser:
    #         loser_id_to_delete = loser.army_id

    #     # --- STEP 5: RE-EMBARK LOGIC ---
    #     if winner.army_type == "SEA":
    #         stmt_ghost = select(Army).where(
    #             Army.house_id == winner.house_id,
    #             Army.army_type == "LAND",
    #             Army.commander_name == winner.commander_name,
    #             Army.status.in_(["MARCHING", "IDLE"]),
    #         )
    #         ghosts = (await self.session.execute(stmt_ghost)).scalars().all()
    #         for g in ghosts:
    #             kill_task(g)
    #             await self.session.delete(g)

    #     # --- STEP 6: IMPROVED REPORT GENERATION ---
    #     def generate_detailed_breakdown(snapshot, current_army, is_deleted):
    #         start_count = snapshot[
    #             "count"
    #         ]  # This is count AFTER rounds, but BEFORE retreat losses

    #         if is_deleted:
    #             # If deleted, they lost everything remaining
    #             ct = (
    #                 f"\n📦 **Cargo:** {snapshot['cargo_count']} Lost/Captured"
    #                 if snapshot["cargo_count"] > 0
    #                 else ""
    #             )
    #             return f"💀 **Wiped Out** (Lost remaining {start_count}){ct}"

    #         if not current_army:
    #             return "Unknown"

    #         end_count = current_army.troop_count
    #         lost_in_retreat = start_count - end_count

    #         # Wording change: "Survivors" instead of "Strength"
    #         header = f"**Survivors:** {end_count}"

    #         if lost_in_retreat > 0:
    #             header += f" (🔻 {lost_in_retreat} lost during retreat)"

    #         lines = [header]

    #         # Current Cargo Status
    #         if snapshot["cargo_count"] > 0:
    #             curr_c = (
    #                 current_army.cargo.get("troop_count", 0)
    #                 if current_army.cargo
    #                 else 0
    #             )
    #             lines.append(f"\n📦 **Cargo:** {curr_c}")

    #         return "\n".join(lines)

    #     att_deleted = (attacker == loser) and should_delete_loser
    #     def_deleted = (defender == loser) and should_delete_loser

    #     att_obj = None if att_deleted else attacker
    #     def_obj = None if def_deleted else defender

    #     att_bd = generate_detailed_breakdown(att_snap, att_obj, att_deleted)
    #     def_bd = generate_detailed_breakdown(def_snap, def_obj, def_deleted)
    #     win_name = winner.commander_name if winner else "None"

    #     final_report = (
    #         f"🏆 **Victor:** **{win_name}**\n\n"
    #         f"**Attacker ({att_snap['name']})**\n{att_bd}\n\n"
    #         f"**Defender ({def_snap['name']})**\n{def_bd}\n\n"
    #         f"💰 **Loot:** The victor seized **{loot}** Gold.\n\n"
    #         f"**Aftermath:**\n{commander_fate_str}"
    #     )

    #     # --- STEP 7: CLEANUP ---
    #     await self.session.delete(battle)

    #     if loser.army_type == "SEA" and (
    #         should_delete_loser or loser.status == "RETREATING"
    #     ):
    #         stmt_ghost = select(Army).where(
    #             Army.house_id == loser.house_id,
    #             Army.army_type == "LAND",
    #             Army.commander_name == loser.commander_name,
    #             Army.status.in_(["MARCHING", "IDLE"]),
    #         )
    #         ghosts = (await self.session.execute(stmt_ghost)).scalars().all()
    #         for g in ghosts:
    #             kill_task(g)
    #             await self.session.delete(g)

    #     await self.session.commit()

    #     if loser_id_to_delete:
    #         try:
    #             await self.session.execute(
    #                 text(
    #                     "DELETE FROM pending_interactions WHERE army1_id = :aid OR army2_id = :aid"
    #                 ),
    #                 {"aid": loser_id_to_delete},
    #             )
    #             await self.session.execute(
    #                 text("DELETE FROM march_logs WHERE army_id = :aid"),
    #                 {"aid": loser_id_to_delete},
    #             )
    #             stmt = delete(Army).where(Army.army_id == loser_id_to_delete)
    #             await self.session.execute(stmt)
    #             await self.session.commit()
    #         except Exception:
    #             await self.session.rollback()

    #     return final_report

    async def resolve_manual_battle_aftermath(self, battle_id: int) -> str:
        """
        Resolves the aftermath for a GM-DRIVEN battle.
        UPDATED: Skips deletion for SIEGE battles so !resolve_siege can run afterwards.
        """
        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(
                selectinload(Battle.attacker)
                .selectinload(Army.house)
                .selectinload(House.game),
                selectinload(Battle.defender).selectinload(Army.house),
                selectinload(Battle.fief),  # Load Fief too just in case
            )
        )
        battle = (await self.session.execute(stmt)).scalars().first()
        if not battle:
            return "Error: Battle not found."

        attacker, defender = battle.attacker, battle.defender
        ship_capacity = battle.game.ship_capacity

        # --- STEP 1: SNAPSHOT DATA ---
        import copy

        def get_snapshot(army):
            if not army:
                return {
                    "exists": False,
                    "count": 0,
                    "cargo_count": 0,
                    "comp": {},
                    "name": "Unknown",
                    "house": "Unknown",
                }
            return {
                "exists": True,
                "count": army.troop_count,
                "cargo_count": army.cargo.get("troop_count", 0) if army.cargo else 0,
                "comp": dict(army.composition or {}),
                "name": army.commander_name,
                "house": army.house.name if army.house else "Unknown",
            }

        att_snap = get_snapshot(attacker)
        def_snap = get_snapshot(defender)

        if battle.attacker_score >= battle.defender_score:
            winner, loser = attacker, defender
        else:
            winner, loser = defender, attacker

        loser_id_to_delete = None

        # --- STEP 2: LOSSES & CARGO CAPACITY CHECK ---
        from sqlalchemy.orm.attributes import flag_modified

        def enforce_cargo_limits(army):
            if army and army.army_type == "SEA" and army.cargo:
                max_cap = army.troop_count * ship_capacity
                current = army.cargo.get("troop_count", 0)
                if current > max_cap:
                    new_count = max_cap
                    new_comp, _ = ArmyRepo._calculate_split(
                        army.cargo.get("composition", {}), new_count, current
                    )
                    nc = dict(army.cargo)
                    nc["troop_count"] = new_count
                    nc["composition"] = new_comp
                    army.cargo = nc
                    flag_modified(army, "cargo")

        enforce_cargo_limits(winner)
        enforce_cargo_limits(loser)

        # --- STEP 3: STOP MOVEMENT TASKS ---
        from app.celery_app import celery_app

        def kill_task(army_obj):
            if not army_obj or not army_obj.task_id:
                return
            try:
                celery_app.control.revoke(army_obj.task_id, terminate=True)
            except:
                pass
            army_obj.task_id = None
            army_obj.destination_x = None
            army_obj.destination_y = None
            army_obj.arrival_time = None
            army_obj.departure_time = None

        kill_task(loser)
        kill_task(winner)

        # --- STEP 4: RETREAT / CAPTURE ---
        commander_fate_str = "The losing army was completely wiped out."
        should_delete_loser = False

        loser_mention = f"**{loser.house.name}**" if loser and loser.house else "Enemy"
        if loser and loser.house_id:
            stmt_user = (
                select(User.discord_id)
                .join(GamePlayer, GamePlayer.user_id == User.user_id)
                .where(
                    GamePlayer.game_id == battle.game_id,
                    GamePlayer.claimed_house_id == loser.house_id,
                )
            )
            discord_id = (await self.session.execute(stmt_user)).scalar()
            if discord_id:
                loser_mention = f"<@{discord_id}>"

        if loser and loser.troop_count > 0:
            loser_martial = await self._get_character_martial(
                loser.house_id, loser.commander_name
            )
            retreat_odds = 40 + (loser_martial * 2)

            if random.randint(1, 100) <= retreat_odds:
                loser.status = "RETREATING"
                # Clear movement
                loser.destination_x = None
                loser.destination_y = None
                loser.arrival_time = None

                unit_term = "ships" if loser.army_type == "SEA" else "troops"
                commander_fate_str = f"**{loser.commander_name}** managed to disengage! The remaining {loser.troop_count} {unit_term} have scattered (Status: **RETREATING**).\n⚠️ {loser_mention} **ACTION REQUIRED:** You must manually move them to safety."
            else:
                # Rout
                sp = 0
                if loser.army_type == "SEA" and loser.cargo:
                    sp = loser.cargo.get("troop_count", 0)
                else:
                    sp = loser.troop_count

                if sp > 0 and winner:
                    if not winner.cargo:
                        winner.cargo = {}
                    if "prisoners" not in winner.cargo:
                        winner.cargo["prisoners"] = []
                    winner.cargo["prisoners"].append(
                        {"house": loser.house.name, "count": sp}
                    )
                    flag_modified(winner, "cargo")

                fate_str = (
                    "Captured" if random.randint(1, 100) <= 50 else "Killed in Action"
                )
                commander_fate_str = f"The retreat failed! **{loser.commander_name}** was **{fate_str}**! {loser_mention}, your forces were lost."
                should_delete_loser = True
        elif loser:
            commander_fate_str = f"The force was completely destroyed! **{loser.commander_name}** was **Killed in Action**!"
            should_delete_loser = True

        # Loot (Only applying if not Siege, usually Siege loot handled in resolve_siege)
        # But we calculate it here for the report.
        loot = loser.treasury if loser else 0
        if winner and winner.house and battle.battle_type != "SIEGE":
            winner_house = await self.session.get(House, winner.house_id)
            if winner_house:
                winner_house.treasury += loot
        if loser:
            loser.treasury = 0

        if loser and loser.troop_count <= 0:
            should_delete_loser = True
        if should_delete_loser and loser:
            loser_id_to_delete = loser.army_id

        # --- STEP 5: RE-EMBARK LOGIC ---
        if winner.army_type == "SEA":
            stmt_ghost = select(Army).where(
                Army.house_id == winner.house_id,
                Army.army_type == "LAND",
                Army.commander_name == winner.commander_name,
                Army.status.in_(["MARCHING", "IDLE"]),
            )
            ghosts = (await self.session.execute(stmt_ghost)).scalars().all()
            for g in ghosts:
                kill_task(g)
                await self.session.delete(g)

        # --- STEP 6: IMPROVED REPORT GENERATION ---
        def generate_detailed_breakdown(snapshot, current_army, is_deleted):
            start_count = snapshot["count"]
            if is_deleted:
                ct = (
                    f"\n**Cargo:** {snapshot['cargo_count']} Lost"
                    if snapshot["cargo_count"] > 0
                    else ""
                )
                return f"💀 **Wiped Out** (Lost remaining {start_count}){ct}"
            if not current_army:
                return "Unknown"

            lines = [f"**Survivors:** {current_army.troop_count}"]
            lost = start_count - current_army.troop_count
            if lost > 0:
                lines[0] += f" (🔻 {lost} lost in retreat)"

            if snapshot["cargo_count"] > 0:
                curr_c = (
                    current_army.cargo.get("troop_count", 0)
                    if current_army.cargo
                    else 0
                )
                lost_c = snapshot["cargo_count"] - curr_c
                lines.append(f"\n📦 **Cargo:** {curr_c}")
            return "\n".join(lines)

        att_deleted = (attacker == loser) and should_delete_loser
        def_deleted = (defender == loser) and should_delete_loser

        att_obj = None if att_deleted else attacker
        def_obj = None if def_deleted else defender

        att_bd = generate_detailed_breakdown(att_snap, att_obj, att_deleted)
        def_bd = generate_detailed_breakdown(def_snap, def_obj, def_deleted)
        win_name = winner.commander_name if winner else "None"

        final_report = (
            f"🏆 **Victor:** **{win_name}**\n\n"
            f"**Attacker ({att_snap['name']})**\n{att_bd}\n\n"
            f"**Defender ({def_snap['name']})**\n{def_bd}\n\n"
            f"💰 **Loot:** The victor seized **{loot}** Gold.\n\n"
            f"**Aftermath:**\n{commander_fate_str}"
        )

        # --- STEP 7: CLEANUP ---

        # --- CRITICAL FIX FOR SIEGES ---
        if battle.battle_type != "SIEGE":
            # Normal Battle: Delete immediately
            await self.session.delete(battle)
        else:
            # Siege: Keep the battle record alive so !resolve_siege can use it
            pass
        # -------------------------------

        if loser.army_type == "SEA" and (
            should_delete_loser or loser.status == "RETREATING"
        ):
            stmt_ghost = select(Army).where(
                Army.house_id == loser.house_id,
                Army.army_type == "LAND",
                Army.commander_name == loser.commander_name,
                Army.status.in_(["MARCHING", "IDLE"]),
            )
            ghosts = (await self.session.execute(stmt_ghost)).scalars().all()
            for g in ghosts:
                kill_task(g)
                await self.session.delete(g)

        await self.session.commit()

        if loser_id_to_delete:
            try:
                await self.session.execute(
                    text(
                        "DELETE FROM pending_interactions WHERE army1_id = :aid OR army2_id = :aid"
                    ),
                    {"aid": loser_id_to_delete},
                )
                await self.session.execute(
                    text("DELETE FROM march_logs WHERE army_id = :aid"),
                    {"aid": loser_id_to_delete},
                )
                stmt = delete(Army).where(Army.army_id == loser_id_to_delete)
                await self.session.execute(stmt)
                await self.session.commit()
            except Exception:
                await self.session.rollback()

        return final_report

    # ======================================================================
    # ===== SYNCHRONOUS METHODS (For Celery Auto-Battle) ===================
    # ======================================================================

    def _get_character_martial_sync(self, house_id: int, commander_name: str) -> int:
        if not commander_name:
            return 0
        stmt = select(Character).where(
            Character.house_id == house_id, Character.name.ilike(commander_name)
        )
        char = self.session.execute(stmt).scalars().first()
        return char.skills.get("martial", 0) if char and char.skills else 0

    def _get_home_fief_sync(self, house_id: int) -> Fief | None:
        stmt = select(Fief).where(Fief.owner_id == house_id).limit(1)
        return self.session.execute(stmt).scalars().first()

    def start_battle_sync(self, game_id, attacker_id, defender_id, ambush, defense):
        attacker, defender = self.session.query(Army).get(
            attacker_id
        ), self.session.query(Army).get(defender_id)
        if not attacker or not defender:
            return None, "Armies not found.", None

        battle_type = "LAND_BATTLE"
        att_martial = self._get_character_martial_sync(
            attacker.house_id, attacker.commander_name
        )
        def_martial = self._get_character_martial_sync(
            defender.house_id, defender.commander_name
        )
        _, att_bp = self._calculate_army_bp(attacker)
        _, def_bp = self._calculate_army_bp(defender)
        att_bonus, def_bonus = att_martial, def_martial
        if attacker.troop_count > defender.troop_count * 1.2:
            att_bonus += 4
        elif defender.troop_count > attacker.troop_count * 1.2:
            def_bonus += 4
        odds = int(max(1, min(99, 50 + ((att_bp + att_bonus) - (def_bp + def_bonus)))))

        new_battle = Battle(
            game_id=game_id,
            attacker_id=attacker.army_id,
            defender_id=defender.army_id,
            current_odds=odds,
            battle_type=battle_type,
        )
        self.session.add(new_battle)

        # --- STOP MOVEMENT NOW ---
        self._stop_movement_immediately(attacker)
        self._stop_movement_immediately(defender)

        self.session.commit()
        return new_battle, f"Attacker Odds: 1 - {odds}", "Sync battle started."

    def process_battle_round_sync(self, battle_id: int):
        battle = self.session.query(Battle).get(battle_id)
        if not battle:
            return None, "Battle not found.", None, None

        if battle.attacker_score >= 5 or battle.defender_score >= 5:
            winner = (
                "Attacker"
                if battle.attacker_score > battle.defender_score
                else "Defender"
            )
            return battle, "Battle has already concluded.", winner, False

        odds_at_start = battle.current_odds
        roll = random.randint(1, 100)
        is_attacker_win = roll <= odds_at_start

        if is_attacker_win:
            battle.attacker_score += 1
            battle.current_odds = min(95, battle.current_odds + 5)
        else:
            battle.defender_score += 1
            battle.current_odds = max(5, battle.current_odds - 5)

        # --- CASUALTY CALCULATION ---
        att_loss_pct = random.uniform(0.01, 0.03)
        def_loss_pct = random.uniform(0.01, 0.03)
        if is_attacker_win:
            def_loss_pct += 0.02
        else:
            att_loss_pct += 0.02

        # 1. Calculate Ship/Troop Losses
        att_losses = int(battle.attacker.troop_count * att_loss_pct)
        def_losses = int(battle.defender.troop_count * def_loss_pct)

        # 2. Apply Ship/Troop Losses
        battle.attacker.troop_count = max(0, battle.attacker.troop_count - att_losses)
        battle.defender.troop_count = max(0, battle.defender.troop_count - def_losses)

        # --- FIX: CARGO ATTRITION (Drowning) ---
        # If ships sank, we must reduce the cargo proportionally NOW.

        def apply_cargo_damage(army, loss_pct):
            if (
                army.army_type == "SEA"
                and army.cargo
                and army.cargo.get("troop_count", 0) > 0
            ):
                c_men = army.cargo["troop_count"]

                # Men die at the same rate as ships
                c_losses = int(c_men * loss_pct)
                c_new = max(0, c_men - c_losses)

                # Recalculate cargo composition
                if c_losses > 0:
                    c_comp, _ = ArmyRepo._calculate_split(
                        army.cargo.get("composition", {}), c_new, c_men
                    )
                    # Trigger SQL Update
                    new_cargo = dict(army.cargo)
                    new_cargo["troop_count"] = c_new
                    new_cargo["composition"] = c_comp
                    army.cargo = new_cargo

        apply_cargo_damage(battle.attacker, att_loss_pct)
        apply_cargo_damage(battle.defender, def_loss_pct)
        # ---------------------------------------

        self.session.commit()

        winner = None
        if battle.attacker_score >= 5:
            winner = "Attacker"
        elif battle.defender_score >= 5:
            winner = "Defender"

        return (
            battle,
            f"Roll: **{roll}** (Target: ≤ {odds_at_start}). Round to **{'Attacker' if is_attacker_win else 'Defender'}**",
            winner,
            False,
        )

    def resolve_aftermath_sync(
        self, battle_id: int
    ) -> tuple[str, int] | tuple[None, None]:
        """
        Handles casualties, cargo loss, cleanup, and RE-EMBARKING.
        FIXED: Uses session.refresh() and flag_modified() to ensure Cargo is updated correctly.
        """
        # 1. Fetch Battle
        battle = (
            self.session.query(Battle)
            .options(
                selectinload(Battle.attacker)
                .selectinload(Army.house)
                .selectinload(House.game),
                selectinload(Battle.defender).selectinload(Army.house),
            )
            .get(battle_id)
        )
        if not battle:
            return None, None

        guild_id = battle.game.guild_id
        attacker, defender = battle.attacker, battle.defender

        # --- CRITICAL FIX 1: Refresh objects to ensure Cargo is loaded fresh ---
        if attacker:
            self.session.refresh(attacker)
        if defender:
            self.session.refresh(defender)

        ship_capacity = battle.game.ship_capacity

        # --- SNAPSHOTS ---
        import copy

        def get_snapshot(army):
            if not army:
                return {
                    "exists": False,
                    "count": 0,
                    "cargo_count": 0,
                    "comp": {},
                    "name": "Unknown",
                    "house": "Unknown",
                }
            return {
                "exists": True,
                "count": army.troop_count,
                "cargo_count": army.cargo.get("troop_count", 0) if army.cargo else 0,
                "comp": dict(army.composition or {}),
                "name": army.commander_name,
                "house": army.house.name if army.house else "Unknown",
            }

        att_snap = get_snapshot(attacker)
        def_snap = get_snapshot(defender)

        loser_id_to_delete = None

        if battle.attacker_score >= battle.defender_score:
            winner, loser = attacker, defender
            win_loss_idx, lose_loss_idx = battle.defender_score, battle.attacker_score
        else:
            winner, loser = defender, attacker
            win_loss_idx, lose_loss_idx = battle.attacker_score, battle.defender_score

        # --- LOSS CALCULATION ---
        win_loss_pct = WINNER_CASUALTY_TABLE.get(win_loss_idx, 0.50)
        lose_loss_pct = LOSER_CASUALTY_TABLE.get(lose_loss_idx, 0.70)

        # Store reduction factors
        winner_reduction = 1.0 - win_loss_pct
        loser_reduction = 1.0 - lose_loss_pct

        from sqlalchemy.orm.attributes import flag_modified

        def apply_losses(army, percent):
            if not army or army.troop_count <= 0:
                return

            reduction = 1.0 - percent

            # 1. Main Troops / Ships
            new_count = int(army.troop_count * reduction)
            new_comp, _ = ArmyRepo._calculate_split(
                army.composition, new_count, army.troop_count
            )
            army.troop_count = new_count
            army.composition = new_comp

            # 2. Cargo Logic (Drowning + Capacity Check)
            if (
                army.army_type == "SEA"
                and army.cargo
                and army.cargo.get("troop_count", 0) > 0
            ):
                c_men = army.cargo["troop_count"]

                # A. Apply Percentage Loss (Drowning)
                c_after_damage = int(c_men * reduction)

                # B. Apply Capacity Limit (Physics check)
                max_capacity = army.troop_count * ship_capacity
                c_final = min(c_after_damage, max_capacity)

                # Calculate composition of survivors
                c_comp, _ = ArmyRepo._calculate_split(
                    army.cargo.get("composition", {}), c_final, c_men
                )

                # --- CRITICAL FIX 2: Explicitly update and Flag Modified ---
                # We create a new dict to ensure Python sees it as a new object
                new_cargo_blob = dict(army.cargo)
                new_cargo_blob["troop_count"] = c_final
                new_cargo_blob["composition"] = c_comp

                army.cargo = new_cargo_blob
                flag_modified(army, "cargo")  # Force SQLAlchemy to save this field

        if winner:
            apply_losses(winner, win_loss_pct)
        if loser:
            apply_losses(loser, lose_loss_pct)

        # --- STOP MOVEMENT ---
        from app.celery_app import celery_app

        def kill_task(army_obj):
            if not army_obj or not army_obj.task_id:
                return
            try:
                celery_app.control.revoke(army_obj.task_id, terminate=True)
            except:
                pass
            army_obj.task_id = None
            army_obj.destination_x = None
            army_obj.destination_y = None
            army_obj.arrival_time = None
            army_obj.departure_time = None

        kill_task(loser)
        kill_task(winner)

        # --- RETREAT / CAPTURE ---
        commander_fate_str = "The losing army was completely wiped out."
        should_delete_loser = False

        loser_mention = f"**{loser.house.name}**" if loser and loser.house else "Enemy"
        if loser and loser.house_id:
            stmt_user = (
                select(User.discord_id)
                .join(GamePlayer, GamePlayer.user_id == User.user_id)
                .where(
                    GamePlayer.game_id == battle.game_id,
                    GamePlayer.claimed_house_id == loser.house_id,
                )
            )
            discord_id = self.session.execute(stmt_user).scalar()
            if discord_id:
                loser_mention = f"<@{discord_id}>"

        if loser and loser.troop_count > 0:
            loser_martial = self._get_character_martial_sync(
                loser.house_id, loser.commander_name
            )
            retreat_odds = 40 + (loser_martial * 2)

            if random.randint(1, 100) <= retreat_odds:
                loser.status = "RETREATING"
                unit_term = "ships" if loser.army_type == "SEA" else "troops"
                commander_fate_str = f"**{loser.commander_name}** managed to disengage! The remaining {loser.troop_count} {unit_term} have scattered (Status: **RETREATING**).\n⚠️ {loser_mention} **ACTION REQUIRED:** You must manually move them to safety."
            else:
                # Rout
                sp = 0
                if loser.army_type == "SEA" and loser.cargo:
                    sp = loser.cargo.get("troop_count", 0)
                else:
                    sp = loser.troop_count

                if sp > 0 and winner:
                    if not winner.cargo:
                        winner.cargo = {}
                    if "prisoners" not in winner.cargo:
                        winner.cargo["prisoners"] = []
                    winner.cargo["prisoners"].append(
                        {"house": loser.house.name, "count": sp}
                    )
                    # Flag modified here too
                    flag_modified(winner, "cargo")

                fate_str = (
                    "Captured" if random.randint(1, 100) <= 50 else "Killed in Action"
                )
                commander_fate_str = f"The retreat failed! **{loser.commander_name}** was **{fate_str}**! {loser_mention}, your forces were lost."
                should_delete_loser = True
        elif loser:
            commander_fate_str = f"The force was completely destroyed! **{loser.commander_name}** was **Killed in Action**!"
            should_delete_loser = True

        loot = loser.treasury if loser else 0
        if winner and winner.house:
            winner_house = self.session.query(House).get(winner.house_id)
            if winner_house:
                winner_house.treasury += loot
        if loser:
            loser.treasury = 0

        if loser and loser.troop_count <= 0:
            should_delete_loser = True
        if should_delete_loser and loser:
            loser_id_to_delete = loser.army_id

        # --- RE-EMBARK LOGIC (FIX FOR EJECTED CARGO) ---
        if winner.army_type == "SEA":
            ghosts = (
                self.session.query(Army)
                .filter(
                    Army.house_id == winner.house_id,
                    Army.army_type == "LAND",
                    Army.commander_name == winner.commander_name,
                    Army.status.in_(["MARCHING", "IDLE"]),
                )
                .all()
            )

            for g in ghosts:
                print(
                    f"[BATTLE] Found Winner's Ghost Army {g.army_id}. Re-embarking survivors."
                )

                initial_troops = g.troop_count
                surviving_troops = int(initial_troops * winner_reduction)
                max_cap = winner.troop_count * ship_capacity
                final_troops = min(surviving_troops, max_cap)

                new_comp, _ = ArmyRepo._calculate_split(
                    g.composition, final_troops, initial_troops
                )

                winner.cargo = {
                    "commander": g.commander_name,
                    "troop_count": final_troops,
                    "composition": new_comp,
                }
                flag_modified(winner, "cargo")  # Flag here too

                kill_task(g)
                self.session.delete(g)

        # --- REPORT GEN ---
        def generate_breakdown(snapshot, current_army, is_deleted):
            before_total = snapshot["count"]
            if is_deleted:
                ct = (
                    f"\n**Cargo:** {snapshot['cargo_count']} Lost"
                    if snapshot["cargo_count"] > 0
                    else ""
                )
                return f"Total: 0/{before_total} (Wiped Out){ct}"
            if not current_army:
                return "Unknown"

            lines = [f"Total: {current_army.troop_count}/{before_total}"]
            for u, i in snapshot["comp"].items():
                s = current_army.composition.get(u, 0)
                lines.append(f"- {u.title()}: {s} (Lost {i-s})")

            # Show updated cargo
            if snapshot["cargo_count"] > 0:
                curr_c = (
                    current_army.cargo.get("troop_count", 0)
                    if current_army.cargo
                    else 0
                )
                lost_c = snapshot["cargo_count"] - curr_c
                lines.append(
                    f"\n**Cargo:** {curr_c}/{snapshot['cargo_count']} (Lost {lost_c})"
                )
            return "\n".join(lines)

        att_deleted = (attacker == loser) and should_delete_loser
        def_deleted = (defender == loser) and should_delete_loser

        att_bd = generate_breakdown(att_snap, attacker, att_deleted)
        def_bd = generate_breakdown(def_snap, defender, def_deleted)
        win_name = winner.commander_name if winner else "None"

        final_report = (
            f"🏆 **Victor:** **{win_name}**\n\n"
            f"**Attacker ({att_snap['name']})**\n{att_bd}\n\n"
            f"**Defender ({def_snap['name']})**\n{def_bd}\n\n"
            f"💰 **Loot:** The victor seized **{loot}** Gold.\n\n"
            f"**Aftermath:**\n{commander_fate_str}"
        )

        # --- CLEANUP ---
        self.session.delete(battle)

        if loser.army_type == "SEA" and (
            should_delete_loser or loser.status == "RETREATING"
        ):
            ghosts = (
                self.session.query(Army)
                .filter(
                    Army.house_id == loser.house_id,
                    Army.army_type == "LAND",
                    Army.commander_name == loser.commander_name,
                    Army.status.in_(["MARCHING", "IDLE"]),
                )
                .all()
            )
            for g in ghosts:
                kill_task(g)
                self.session.delete(g)

        self.session.commit()

        if loser_id_to_delete:
            try:
                self.session.execute(
                    text(
                        "DELETE FROM pending_interactions WHERE army1_id = :aid OR army2_id = :aid"
                    ),
                    {"aid": loser_id_to_delete},
                )
                self.session.execute(
                    text("DELETE FROM march_logs WHERE army_id = :aid"),
                    {"aid": loser_id_to_delete},
                )
                stmt = delete(Army).where(Army.army_id == loser_id_to_delete)
                self.session.execute(stmt)
                self.session.commit()
            except Exception as e:
                self.session.rollback()
                print(
                    f"⚠️ Warning: Could not delete defeated army {loser_id_to_delete}: {e}"
                )

        return final_report, guild_id

    async def start_siege(
        self, game_id: int, attacker_id: int, fief_name: str, defense_bonus_str: str
    ):
        """
        Initiates a Siege.
        FIXED:
        1. Defenders must be LAND armies (Fleets cannot defend walls).
        2. Eagerly loads 'fief' to prevent UI crashes.s
        3. Stops the attacker's movement immediately.
        """
        # 1. Fetch Attacker
        stmt_att = (
            select(Army)
            .where(Army.army_id == attacker_id)
            .options(selectinload(Army.house).selectinload(House.characters))
        )
        attacker = (await self.session.execute(stmt_att)).scalars().first()
        if not attacker:
            return None, "❌ Army not found.", None

        # 2. Fetch Fief
        stmt_f = select(Fief).where(Fief.game_id == game_id, Fief.name.ilike(fief_name))
        fief = (await self.session.execute(stmt_f)).scalars().first()
        if not fief:
            return None, "❌ Fief not found.", None

        # 3. Fetch Defender (Garrison) - LAND ONLY
        stmt_d = (
            select(Army)
            .where(
                Army.house_id == fief.owner_id,
                Army.location_x == fief.location_x,
                Army.location_y == fief.location_y,
                Army.status == "GARRISONED",
                Army.army_type == "LAND",  # <--- CRITICAL FIX: Ignore Fleets
            )
            .options(selectinload(Army.house).selectinload(House.characters))
        )
        defender = (await self.session.execute(stmt_d)).scalars().first()

        if not defender:
            return (
                None,
                "❌ No Land Garrison found to siege. (Fleets cannot defend walls).",
                None,
            )

        # 4. Calculate Stats & Odds
        att_val, att_bp = self._calculate_army_bp(attacker)
        def_val, def_bp = self._calculate_army_bp(defender)

        att_martial = await self._get_character_martial(
            attacker.house_id, attacker.commander_name
        )
        def_martial = await self._get_character_martial(
            defender.house_id, defender.commander_name
        )

        def_bonuses = {"major": 20, "significant": 10, "minor": 5, "siege_camp": 3}
        def_bonus = def_bonuses.get(defense_bonus_str.lower(), 0)

        att_total = att_bp + att_martial
        def_total = def_bp + def_martial + def_bonus

        odds = 50 + (att_total - def_total)
        odds = int(max(10, min(90, odds)))

        # 5. Create Battle Record
        new_battle = Battle(
            game_id=game_id,
            attacker_id=attacker.army_id,
            defender_id=defender.army_id,
            battle_type="SIEGE",
            siege_phase="WALLS",
            fief_id=fief.fief_id,
            current_odds=odds,
        )
        self.session.add(new_battle)

        # Stop movement
        self._stop_movement_immediately(attacker)

        await self.session.commit()

        # Re-load Battle with Fief Relation
        stmt_reload = (
            select(Battle)
            .where(Battle.id == new_battle.id)
            .options(
                selectinload(Battle.fief),
                selectinload(Battle.attacker).selectinload(Army.house),
                selectinload(Battle.defender).selectinload(Army.house),
            )
        )
        loaded_battle = (await self.session.execute(stmt_reload)).scalars().first()

        calc_log = (
            f"**Attacker:** Units `{att_bp:.1f}` + Mar `{att_martial}`\n"
            f"**Defender:** Units `{def_bp:.1f}` + Mar `{def_martial}` + Wall `{def_bonus}`\n"
            f"**Formula:** 50 + {att_total:.1f} - {def_total:.1f} = **{odds}**"
        )

        return loaded_battle, f"Attacker Odds (Walls): 1 - {odds}", calc_log

    async def resolve_siege_consequences(self, battle_id: int):
        """
        Finalizes a siege.
        UPDATED: Captures/Transfers ALL other assets (Fleets/Armies) at the location.
        """
        # 1. Load Battle
        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(
                selectinload(Battle.fief),
                selectinload(Battle.attacker).selectinload(Army.house),
                selectinload(Battle.defender).selectinload(Army.house),
            )
        )
        battle = (await self.session.execute(stmt)).scalars().first()

        if not battle or battle.battle_type != "SIEGE":
            return False, "Invalid siege."

        fief = battle.fief
        fief_name = fief.name if fief else "Unknown Fief"

        if battle.attacker_score >= battle.defender_score:
            winner = battle.attacker
            is_attacker_win = True
        else:
            winner = battle.defender
            is_attacker_win = False

        if not fief or not winner:
            await self.session.delete(battle)
            await self.session.commit()
            return False, "Data missing. Battle deleted."

        # --- SCENARIO A: DEFENDER WINS ---
        if not is_attacker_win:
            await self.session.delete(battle)
            await self.session.commit()
            return (
                True,
                f"🛡️ **Siege Repelled!** The defenders of **{fief_name}** have held strong!",
            )

        # --- SCENARIO B: ATTACKER WINS ---
        victim_house_id = fief.owner_id
        victim_house = await self.session.get(House, victim_house_id)
        attacker_house = winner.house
        attacker_house_name = attacker_house.name if attacker_house else "Unknown"

        # 1. Loot
        loot = victim_house.treasury if victim_house else 0
        if attacker_house:
            attacker_house.treasury += loot
        if victim_house:
            victim_house.treasury = 0

        # 2. Transfer Fief
        fief.owner_id = winner.house_id
        fief.integration = 0.10

        # 3. Garrison Winner
        winner.location_x = fief.location_x
        winner.location_y = fief.location_y
        winner.status = "GARRISONED"
        winner.commander_name = f"Garrison of {fief_name}"
        self._stop_movement_immediately(winner)

        # 4. Wipe the primary Defender (The one that fought)
        if battle.defender:
            await self.session.delete(battle.defender)

        # --- 5. CAPTURE REMAINING ASSETS (THE FIX) ---
        # Find any OTHER armies/fleets owned by the victim at this location
        stmt_assets = select(Army).where(
            Army.house_id == victim_house_id,
            Army.location_x == fief.location_x,
            Army.location_y == fief.location_y,
            Army.army_id != battle.defender_id,  # Skip the one we just deleted
        )
        captured_assets = (await self.session.execute(stmt_assets)).scalars().all()

        captured_text = ""
        for asset in captured_assets:
            # Transfer ownership
            asset.house_id = winner.house_id

            # Update status
            if asset.army_type == "SEA":
                asset.status = "DOCKED"  # Fleets become docked
                asset.commander_name = f"Captured Fleet ({asset.troop_count})"
                captured_text += f"\n⚓ **Captured Fleet:** {asset.troop_count} Ships"
            else:
                asset.status = "GARRISONED"  # Land armies join garrison
                asset.commander_name = f"Captured Garrison ({asset.troop_count})"
                captured_text += f"\n🏳️ **Captured Army:** {asset.troop_count} Troops"

        # 6. Cleanup
        await self.session.delete(battle)
        await self.session.commit()

        return True, (
            f"🏰 **{fief_name} has fallen!**\n"
            f"It is now held by **House {attacker_house_name}**.\n"
            f"💰 Treasury seized: **{loot}** Gold.\n"
            f"📉 Integration reset to **10%**.{captured_text}"
        )

    async def occupy_fief(self, game_id: int, user_id: int, army_id: int):
        """
        Allows an army to instantly capture a Fief if it is undefended (No Land Garrison).
        Triggers all standard Conquest logic (Loot, Asset Seizure, Garrisoning).
        """
        # 1. Load Army & Validate
        army = await ArmyRepo.get_army_by_id(self.session, army_id)
        if not army:
            return False, "❌ Army not found."

        # Check ownership
        player = await self.session.scalar(
            select(GamePlayer).where(
                GamePlayer.user_id == user_id, GamePlayer.game_id == game_id
            )
        )
        if not player or army.house_id != player.claimed_house_id:
            return False, "❌ Not your army."

        if army.army_type != "LAND":
            return False, "❌ Only Land armies can occupy castles."

        # 2. Check Location for Fief
        stmt_fief = select(Fief).where(
            Fief.game_id == game_id,
            Fief.location_x == army.location_x,
            Fief.location_y == army.location_y,
        )
        fief = (await self.session.execute(stmt_fief)).scalars().first()

        if not fief:
            return False, "❌ There is no Fief at this location."

        if fief.owner_id == army.house_id:
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
        attacker_house = await self.session.get(House, army.house_id)

        # Loot
        loot = victim_house.treasury if victim_house else 0
        if attacker_house:
            attacker_house.treasury += loot
        if victim_house:
            victim_house.treasury = 0

        # Transfer Fief
        fief.owner_id = army.house_id
        fief.integration = 0.10

        # Garrison Attacker
        army.status = "GARRISONED"
        army.commander_name = f"Garrison of {fief.name}"

        # 5. Asset Seizure (Capture Fleets/Idle Armies)
        stmt_assets = select(Army).where(
            Army.house_id == victim_house.house_id if victim_house else -1,
            Army.location_x == fief.location_x,
            Army.location_y == fief.location_y,
        )
        assets = (await self.session.execute(stmt_assets)).scalars().all()

        captured_text = ""
        for asset in assets:
            asset.house_id = army.house_id
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
            f"💰 Treasury seized: **{loot}** Gold.\n"
            f"📉 Integration reset to **10%**.{captured_text}"
        )
