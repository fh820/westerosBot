import random
import datetime
import asyncio
from sqlalchemy import select, delete, text
from sqlalchemy.orm import selectinload, Session
from app.db.repositories import ArmyRepo, FiefRepo
from app.db.models import Army, Character, House, Battle, Fief, GamePlayer, User
from app.services.engine_manager import PF_ENGINE
from app.services.chronicler import generate_battle_narration
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select, delete, or_
from sqlalchemy.orm import selectinload
from app.db.models import Battle, Army, User, GamePlayer, PendingInteraction

# --- CONFIGURATION ---
UNIT_STATS = {
    "knights": {"value": 15.0},
    "cavalry": {"value": 5.0},
    "infantry": {"value": 3.5},
    "archers": {"value": 2.5},
    "militia": {"value": 1.0},
    "warships": {"value": 100.0},
    "ships": {"value": 100.0},
    "ship": {"value": 100.0},
}

# WINNERS: Lose more in close battles (0), lose very little in crushing victories (5)
WINNER_CASUALTY_TABLE = {
    0: 0.25,
    1: 0.20,
    2: 0.15,
    3: 0.10,
    4: 0.07,
    5: 0.05,
}

# LOSERS: Lose half their army in a retreat (0), lose almost everything in a slaughter (5)
LOSER_CASUALTY_TABLE = {
    0: 0.40,
    1: 0.50,
    2: 0.60,
    3: 0.70,
    4: 0.80,
    5: 0.90,
}

# SEA TABLES (20% reduction from Land values)
SEA_WINNER_CASUALTY_TABLE = {
    0: 0.20,
    1: 0.16,
    2: 0.12,
    3: 0.08,
    4: 0.05,
    5: 0.04,
}
SEA_LOSER_CASUALTY_TABLE = {
    0: 0.32,
    1: 0.40,
    2: 0.48,
    3: 0.56,
    4: 0.64,
    5: 0.72,
}

LAND_AMBUSH_BONUSES = {"extreme": 15, "good": 10, "decent": 5, "failed": -5}
LAND_DEFENSE_BONUSES = {"major": 20, "significant": 10, "minor": 5}
BATTLE_OUTNUMBER_THRESHOLD = 2.0
BATTLE_OUTNUMBER_BONUS = 4
BATTLE_ODDS_MIN = 1
BATTLE_ODDS_MAX = 99
BATTLE_MOMENTUM_PER_SCORE = 5


class BattleService:
    def __init__(self, session: Session):
        self.session = session

    # ====================================================================
    # ===== SHARED HELPERS ===============================================
    # ====================================================================

    async def _get_army_martial(self, army: Army) -> int:
        """
        Determines the martial score for an army based on hierarchy:
        1. Specific GM Override (commander_martial on Army table)
        2. The Player's active character stats (if player owned)
        3. 0 (Default NPC)
        """
        # 1. Check for GM Override / Specific Commander Stat
        if army.commander_martial is not None:
            return army.commander_martial

        # 2. Check for Player Owner
        # We need to fetch the GamePlayer linked to this House
        stmt = (
            select(GamePlayer)
            .where(
                GamePlayer.game_id == army.game_id,
                GamePlayer.claimed_house_id == army.house_id,
                GamePlayer.is_primary == True,
            )
            .options(selectinload(GamePlayer.character))
        )
        player = (await self.session.execute(stmt)).scalars().first()

        if player and player.character:
            return player.character.skills.get("martial", 0)

        # 3. Default
        return 0

    def _scale_composition(self, army, new_total_count: int):
        """
        Updates army.composition to match a new (lower) total troop_count.
        """
        current_total = sum(army.composition.values())
        if current_total <= 0 or new_total_count <= 0:
            army.composition = {}
            return

        ratio = new_total_count / current_total
        new_comp = {}
        running_sum = 0

        # Scale each unit type
        for unit, count in army.composition.items():
            new_val = int(count * ratio)
            new_comp[unit] = new_val
            running_sum += new_val

        # Fix rounding errors (add remainder to the largest group, usually infantry)
        remainder = new_total_count - running_sum
        if remainder > 0:
            # Find the unit type with the most troops to dump the remainder into
            largest_unit = max(new_comp, key=new_comp.get) if new_comp else "infantry"
            new_comp[largest_unit] = new_comp.get(largest_unit, 0) + remainder

        army.composition = new_comp
        flag_modified(army, "composition")

    def _calculate_army_bp(self, army):
        if not army or not army.composition:
            return 0, 0
        total_value = sum(
            count * UNIT_STATS.get(unit.lower(), {}).get("value", 0)
            for unit, count in army.composition.items()
        )
        return total_value, total_value / 250.0

    def _get_field_battle_type(self, attacker: Army, defender: Army):
        if attacker.army_type != defender.army_type:
            return None
        return "SEA_BATTLE" if attacker.army_type == "SEA" else "LAND_BATTLE"

    def _apply_outnumbering_bonus(self, attacker: Army, defender: Army, att_bonus, def_bonus):
        if attacker.troop_count > defender.troop_count * BATTLE_OUTNUMBER_THRESHOLD:
            att_bonus += BATTLE_OUTNUMBER_BONUS
        elif defender.troop_count > attacker.troop_count * BATTLE_OUTNUMBER_THRESHOLD:
            def_bonus += BATTLE_OUTNUMBER_BONUS
        return att_bonus, def_bonus

    def _odds_from_totals(self, att_total, def_total, min_odds=BATTLE_ODDS_MIN, max_odds=BATTLE_ODDS_MAX):
        if att_total + def_total == 0:
            odds = 50
        else:
            odds = (att_total / (att_total + def_total)) * 100
        return int(max(min_odds, min(max_odds, odds)))

    async def _calculate_field_battle_odds(
        self,
        attacker: Army,
        defender: Army,
        battle_type: str,
        ambush: str = "none",
        defense: str = "none",
        att_bonus_override: int = 0,
        def_bonus_override: int = 0,
        att_cmd_override=None,
        def_cmd_override=None,
        score_diff: int = 0,
    ):
        _, att_bp = self._calculate_army_bp(attacker)
        _, def_bp = self._calculate_army_bp(defender)

        att_martial = (
            att_cmd_override
            if att_cmd_override is not None
            else await self._get_army_martial(attacker)
        )
        def_martial = (
            def_cmd_override
            if def_cmd_override is not None
            else await self._get_army_martial(defender)
        )

        att_bonus = (att_martial / 3.0) + att_bonus_override
        def_bonus = (def_martial / 3.0) + def_bonus_override

        if battle_type == "LAND_BATTLE":
            att_bonus += LAND_AMBUSH_BONUSES.get((ambush or "none").lower(), 0)
            def_bonus += LAND_DEFENSE_BONUSES.get((defense or "none").lower(), 0)

        att_bonus, def_bonus = self._apply_outnumbering_bonus(
            attacker, defender, att_bonus, def_bonus
        )

        odds = self._odds_from_totals(att_bp + att_bonus, def_bp + def_bonus)
        odds = int(
            max(
                BATTLE_ODDS_MIN,
                min(BATTLE_ODDS_MAX, odds + (score_diff * BATTLE_MOMENTUM_PER_SCORE)),
            )
        )
        return odds, att_bp, def_bp, att_bonus, def_bonus

    def _stop_movement_immediately(self, army):
        """
        Stops an army in its tracks.
        1. Revokes the Celery arrival task.
        2. Clears destination data.
        """
        if not army:
            return

        if army.task_id:
            from app.celery_app import celery_app

            try:
                celery_app.control.revoke(army.task_id, terminate=True)
            except Exception as e:
                print(f"[BATTLE] Failed to revoke task: {e}")
            army.task_id = None

        army.destination_x = None
        army.destination_y = None
        army.arrival_time = None
        army.departure_time = None

    async def _get_character_martial(self, house_id: int, commander_name: str) -> int:
        if not commander_name:
            return 0
        stmt = select(Character).where(
            Character.house_id == house_id, Character.name.ilike(commander_name)
        )
        char = (await self.session.execute(stmt)).scalars().first()
        return char.skills.get("martial", 0) if char and char.skills else 0

    async def calculate_current_odds(
        self,
        battle_id,
        att_bonus,
        def_bonus,
        att_cmd_override=None,
        def_cmd_override=None,
    ):
        """Helper to recalculate and update odds based on GM modifiers."""
        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(
                selectinload(Battle.attacker)
                .selectinload(Army.house)
                .selectinload(House.characters),
                selectinload(Battle.defender)
                .selectinload(Army.house)
                .selectinload(House.characters),
                selectinload(Battle.fief),
            )
        )
        battle = (await self.session.execute(stmt)).scalars().first()
        if not battle:
            return None

        attacker, defender = battle.attacker, battle.defender
        battle_type = self._get_field_battle_type(attacker, defender)
        if not battle_type:
            return None

        battle.current_odds, _, _, _, _ = await self._calculate_field_battle_odds(
            attacker,
            defender,
            battle_type,
            att_bonus_override=att_bonus,
            def_bonus_override=def_bonus,
            att_cmd_override=att_cmd_override,
            def_cmd_override=def_cmd_override,
            score_diff=battle.attacker_score - battle.defender_score,
        )
        await self.session.commit()
        return battle

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

        battle_type = self._get_field_battle_type(attacker, defender)
        odds, att_bp, def_bp, att_bonus, def_bonus = (
            await self._calculate_field_battle_odds(
                attacker, defender, battle_type, ambush=ambush, defense=defense
            )
        )

        new_battle = Battle(
            game_id=game_id,
            attacker_id=attacker.army_id,
            defender_id=defender.army_id,
            current_odds=odds,
            battle_type=battle_type,
            att_start_count=attacker.troop_count,
            def_start_count=defender.troop_count,
            att_start_cargo_count=(
                attacker.cargo.get("troop_count", 0) if attacker.cargo else 0
            ),
            def_start_cargo_count=(
                defender.cargo.get("troop_count", 0) if defender.cargo else 0
            ),
        )
        self.session.add(new_battle)
        self._stop_movement_immediately(attacker)
        self._stop_movement_immediately(defender)

        await self.session.commit()

        stmt_reload = (
            select(Battle)
            .where(Battle.id == new_battle.id)
            .options(
                selectinload(Battle.game),
                selectinload(Battle.attacker).selectinload(Army.house),
                selectinload(Battle.defender).selectinload(Army.house),
            )
        )
        reloaded_battle = (await self.session.execute(stmt_reload)).scalars().first()

        calc_log = f"**Attacker:** Units `{att_bp:.1f}` + Bonus `{att_bonus}`\n**Defender:** Units `{def_bp:.1f}` + Bonus `{def_bonus}`"
        return reloaded_battle, f"Attacker Odds: 1 - {odds}", calc_log

    async def process_battle_round(self, battle_id: int):
        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(
                selectinload(Battle.game),
                selectinload(Battle.fief),
                # --- FIX: Load these so the AI Chronicler doesn't crash the bot ---
                selectinload(Battle.attacker).selectinload(Army.house),
                selectinload(Battle.defender).selectinload(Army.house),
            )
        )
        battle = (await self.session.execute(stmt)).scalars().first()
        if not battle:
            return None, "Battle not found.", None, False, None, None

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

        stmt_armies = (
            select(Army)
            .where(Army.army_id.in_([battle.attacker_id, battle.defender_id]))
            .options(selectinload(Army.house))
        )
        armies = (await self.session.execute(stmt_armies)).scalars().all()
        attacker_army = next(
            (a for a in armies if a.army_id == battle.attacker_id), None
        )
        defender_army = next(
            (a for a in armies if a.army_id == battle.defender_id), None
        )

        if not attacker_army or not defender_army:
            return battle, "Armies missing", None, False, "Error", {}

        # --- LOGIC ---
        roll = random.randint(1, 100)
        is_attacker_win = roll <= battle.current_odds

        if is_attacker_win:
            battle.attacker_score += 1
            battle.current_odds = min(95, battle.current_odds + 5)
        else:
            battle.defender_score += 1
            battle.current_odds = max(5, battle.current_odds - 5)

        phase_transition = False
        if (
            battle.battle_type == "SIEGE"
            and battle.siege_phase == "WALLS"
            and battle.attacker_score >= 5
        ):
            phase_transition = True
            battle.siege_phase = "STREETS"
            battle.attacker_score, battle.defender_score = 0, 0
            battle.current_odds = 75
            is_attacker_win = None

        # --- START: NEW UNIFIED CASUALTY LOGIC ---

        # 1. Determine loss percentages
        att_loss_pct = 0
        def_loss_pct = 0
        losing_side_bonus_pct = 0

        if is_attacker_win is True:
            def_loss_pct += losing_side_bonus_pct
        elif is_attacker_win is False:
            att_loss_pct += losing_side_bonus_pct

        # 2. Reusable function to apply casualties proportionally
        def apply_casualties(army, loss_pct, lost_round):
            if army.troop_count <= 0:
                return 0  # No troops/ships to lose

            # Calculate unit losses (this is ships for a fleet, or men for a land army)
            unit_losses = int(army.troop_count * loss_pct)

            # FIX: To prevent rounding to zero on small fleets, ensure the loser
            # always loses at least one unit if they have any left.
            # if lost_round and unit_losses == 0 and army.troop_count > 0:
            #     unit_losses = 1

            # Cap losses at the total number of units
            unit_losses = min(unit_losses, army.troop_count)

            # If it's a sea battle with cargo, calculate proportional cargo loss
            if (
                army.army_type == "SEA"
                and army.cargo
                and army.cargo.get("troop_count", 0) > 0
            ):
                initial_ships = army.troop_count
                initial_cargo = army.cargo.get("troop_count", 0)

                new_ship_count = max(0, initial_ships - unit_losses)

                # The percentage of ships that survive is the percentage of cargo that should survive
                survival_rate = (
                    new_ship_count / initial_ships if initial_ships > 0 else 0
                )
                new_cargo_count = int(initial_cargo * survival_rate)

                # Update cargo object
                if new_cargo_count < initial_cargo:
                    c_comp, _ = ArmyRepo._calculate_split(
                        army.cargo.get("composition", {}),
                        new_cargo_count,
                        initial_cargo,
                    )
                    new_cargo_obj = dict(army.cargo)
                    new_cargo_obj["troop_count"] = new_cargo_count
                    new_cargo_obj["composition"] = c_comp
                    army.cargo = new_cargo_obj
                    flag_modified(army, "cargo")

            # Apply the final unit losses (ships or men)
            army.troop_count = max(0, army.troop_count - unit_losses)
            return unit_losses

        # 3. Apply the new function
        att_losses = apply_casualties(
            attacker_army, att_loss_pct, is_attacker_win is False
        )
        def_losses = apply_casualties(
            defender_army, def_loss_pct, is_attacker_win is True
        )

        # --- END: NEW UNIFIED CASUALTY LOGIC ---

        round_winner_name = "Nobody"
        if is_attacker_win is True:
            round_winner_name = attacker_army.commander_name
        elif is_attacker_win is False:
            round_winner_name = defender_army.commander_name

        narration = f"The forces of **{round_winner_name}** push forward!"

        try:
            ai_narration = await generate_battle_narration(
                battle, roll, is_attacker_win, att_losses, def_losses
            )
            if ai_narration:
                narration = ai_narration
        except:
            pass

        await self.session.commit()

        winner = None
        if battle.defender_score >= 5:
            winner = "Defender"
        elif battle.attacker_score >= 5:
            if battle.battle_type == "SIEGE" and battle.siege_phase == "STREETS":
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

    async def resolve_manual_battle_aftermath(
        self, battle_id: int
    ) -> tuple[str, int, dict]:
        """
        Resolves the final state of a battle.
        Applies heavy rout casualties based on victory intensity before handling fate.
        """
        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(selectinload(Battle.game), selectinload(Battle.fief))
        )
        battle = (await self.session.execute(stmt)).scalars().first()
        if not battle:
            return "Error: Battle not found.", None, {}

        guild_id = battle.game.guild_id

        stmt_armies = (
            select(Army)
            .where(Army.army_id.in_([battle.attacker_id, battle.defender_id]))
            .options(selectinload(Army.house))
        )
        armies = (await self.session.execute(stmt_armies)).scalars().all()
        attacker_army = next(
            (a for a in armies if a.army_id == battle.attacker_id), None
        )
        defender_army = next(
            (a for a in armies if a.army_id == battle.defender_id), None
        )

        if not attacker_army or not defender_army:
            return "Error: Armies missing from DB.", guild_id, {}

        # 1. Determine Winner and Loser
        if battle.attacker_score >= battle.defender_score:
            winner, loser = attacker_army, defender_army
            loser_score = battle.defender_score
        else:
            winner, loser = defender_army, attacker_army
            loser_score = battle.attacker_score

        # 2. Snapshot Initial States
        attacker_snapshot = {
            "initial_units": battle.att_start_count,
            "initial_cargo": getattr(battle, "att_start_cargo_count", 0),
            "name": attacker_army.commander_name,
            "house": attacker_army.house.name if attacker_army.house else "Unknown",
        }
        defender_snapshot = {
            "initial_units": battle.def_start_count,
            "initial_cargo": getattr(battle, "def_start_cargo_count", 0),
            "name": defender_army.commander_name,
            "house": defender_army.house.name if defender_army.house else "Unknown",
        }

        # 3. Halt all movement
        self._stop_movement_immediately(loser)
        self._stop_movement_immediately(winner)

        # ====================================================================
        # === HEAVY ROUT CASUALTIES LOGIC ====================================
        # ====================================================================

        # Calculate victory intensity (Index 0-5)
        score_index = max(0, min(5, 5 - loser_score))

        # Pull percentages from config (ensure these dicts exist in your class or global)
        if battle.battle_type == "SEA_BATTLE":
            win_pct = SEA_WINNER_CASUALTY_TABLE.get(score_index, 0.04)
            los_pct = SEA_LOSER_CASUALTY_TABLE.get(score_index, 0.32)
        else:
            # Land or Siege
            win_pct = WINNER_CASUALTY_TABLE.get(score_index, 0.05)
            los_pct = LOSER_CASUALTY_TABLE.get(score_index, 0.45)

        def apply_rout_losses(army, loss_pct, is_loser):
            if army.troop_count <= 0:
                return 0

            units_lost = int(army.troop_count * loss_pct)

            # Ensure the loser always loses at least 1 unit if any remain
            if is_loser and units_lost == 0 and army.troop_count > 0:
                units_lost = 1

            units_lost = min(units_lost, army.troop_count)

            # Cargo Proportional Loss (SEA)
            if (
                army.army_type == "SEA"
                and army.cargo
                and army.cargo.get("troop_count", 0) > 0
            ):
                initial_ships = army.troop_count
                survival_rate = (
                    (initial_ships - units_lost) / initial_ships
                    if initial_ships > 0
                    else 0
                )

                old_cargo_count = army.cargo["troop_count"]
                new_cargo_count = int(old_cargo_count * survival_rate)

                if new_cargo_count < old_cargo_count:
                    # Assuming ArmyRepo logic is available or imported
                    from app.db.repositories import ArmyRepo

                    c_comp, _ = ArmyRepo._calculate_split(
                        army.cargo.get("composition", {}),
                        new_cargo_count,
                        old_cargo_count,
                    )
                    new_cargo_obj = dict(army.cargo)
                    new_cargo_obj["troop_count"] = new_cargo_count
                    new_cargo_obj["composition"] = c_comp
                    army.cargo = new_cargo_obj
                    flag_modified(army, "cargo")

            army.troop_count -= units_lost
            self._scale_composition(army, army.troop_count)
            return units_lost

        # Execute the rout
        apply_rout_losses(winner, win_pct, False)
        apply_rout_losses(loser, los_pct, True)

        commander_fate_str = ""
        is_destroyed = False
        is_siege_victory = battle.battle_type == "SIEGE" and winner == attacker_army

        # 4. Fetch Loser's ID and Locked Quarters for notification
        stmt_user = (
            select(User.discord_id, GamePlayer.private_channel_id)
            .join(GamePlayer, GamePlayer.user_id == User.user_id)
            .where(
                GamePlayer.game_id == battle.game_id,
                GamePlayer.claimed_house_id == loser.house_id,
            )
        )
        loser_data = (await self.session.execute(stmt_user)).first()
        loser_mention = (
            f"<@{loser_data.discord_id}>" if loser_data else f"**{loser.house.name}**"
        )

        # 5. Handle Fate
        if is_siege_victory:
            is_destroyed = True
            commander_fate_str = f"The garrison of **{battle.fief.name}** was overrun! Survivors were put to the sword or captured."
        else:
            if loser.troop_count > 0:
                loser_martial = await self._get_army_martial(loser)
                retreat_odds = 40 + (loser_martial * 2)

                if random.randint(1, 100) <= retreat_odds:
                    is_destroyed = False
                    loser.status = "RETREATING"
                    unit_term = "ships" if loser.army_type == "SEA" else "troops"
                    commander_fate_str = f"**{loser.commander_name}** managed to disengage! The remaining {loser.troop_count} {unit_term} have scattered.\n⚠️ {loser_mention} **ORDERS REQUIRED:** Your host is now **RETREATING** and requires a new destination."
                else:
                    is_destroyed = True
                    fate_roll = random.randint(1, 100)
                    if fate_roll >= 95:
                        fate_str = "Killed in Action"
                    else:
                        fate_str = "Captured"
                    commander_fate_str = f"The retreat failed! **{loser.commander_name}** was **{fate_str}**! {loser_mention}, your host was lost."
            else:
                is_destroyed = True
                commander_fate_str = f"The force was completely destroyed! **{loser.commander_name}** was **Killed in Action**!"

        # 6. Take Prisoners & Loot
        if is_destroyed:
            prisoner_count = loser.troop_count + (
                loser.cargo.get("troop_count", 0) if loser.cargo else 0
            )
            if prisoner_count > 0 and winner:
                if not winner.cargo:
                    winner.cargo = {}
                if "prisoners" not in winner.cargo:
                    winner.cargo["prisoners"] = []
                winner.cargo["prisoners"].append(
                    {"house": loser.house.name, "count": prisoner_count}
                )
                flag_modified(winner, "cargo")

            loser.troop_count = 0
            if loser.army_type == "SEA":
                loser.cargo = None
                flag_modified(loser, "cargo")

        loot = loser.treasury or 0
        if winner.house:
            winner.house.treasury += loot
        loser.treasury = 0

        # 7. Generate Detailed Breakdown
        def generate_bd(snapshot, current_army, is_loser_and_destroyed):
            survivors = 0 if is_loser_and_destroyed else current_army.troop_count
            lines = [f"**Survivors:** {survivors}/{snapshot['initial_units']}"]
            if snapshot["initial_cargo"] > 0:
                curr_c = (
                    current_army.cargo.get("troop_count", 0)
                    if current_army.cargo and not is_loser_and_destroyed
                    else 0
                )
                lines.append(f"📦 **Cargo:** {curr_c}/{snapshot['initial_cargo']}")
            return "\n".join(lines)

        att_is_destroyed_loser = attacker_army == loser and is_destroyed
        def_is_destroyed_loser = defender_army == loser and is_destroyed

        final_report = (
            f"🏆 **Victor:** **{winner.commander_name}**\n\n"
            f"**Attacker ({attacker_snapshot['name']})**\n{generate_bd(attacker_snapshot, attacker_army, att_is_destroyed_loser)}\n\n"
            f"**Defender ({defender_snapshot['name']})**\n{generate_bd(defender_snapshot, defender_army, def_is_destroyed_loser)}\n\n"
            f"💰 **Seized Loot:** {loot} Gold\n\n"
            f"**Aftermath:**\n{commander_fate_str}"
        )

        # 8. Database Cleanup (CRITICAL FIX: ORDER MATTERS)
        try:
            if not is_siege_victory:
                # STEP 1: Delete Pending Interactions referencing the loser
                # This unlocks the army from the "pending_interactions" table
                if is_destroyed and loser:
                    await self.session.execute(
                        delete(PendingInteraction).where(
                            or_(
                                PendingInteraction.army1_id == loser.army_id,
                                PendingInteraction.army2_id == loser.army_id,
                            )
                        )
                    )

                # STEP 2: Delete the Battle Record
                # This unlocks the army from the "battles" table
                await self.session.delete(battle)

                # STEP 3: Delete the Army
                # Now that all references are gone, we can safely delete the army
                if is_destroyed and loser:
                    await self.session.delete(loser)
            else:
                final_report += "\n\n(🏰 **Siege Ended!** Use `!resolve_siege [ID]` to finalize results.)"

            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            return f"Error processing battle: {e}", guild_id, {}

        # 9. Meta-data for Cog
        notif_data = {
            "loser_discord_id": loser_data.discord_id if loser_data else None,
            "loser_channel_id": loser_data.private_channel_id if loser_data else None,
            "is_retreat": not is_destroyed,
        }

        return final_report, guild_id, notif_data

    # ======================================================================
    # ===== AUTO-BATTLE METHODS (Explicit Implementation for Celery) =======
    # ======================================================================

    async def start_auto_battle(
        self, game_id, attacker_id, defender_id, ambush, defense
    ):
        """
        Starts an auto-resolved battle.
        UPDATED: Now uses 1:1 math with Manual Battle for Initial Odds.
        """
        stmt = (
            select(Army)
            .where(Army.army_id.in_([attacker_id, defender_id]))
            .options(selectinload(Army.house))
        )
        armies = (await self.session.execute(stmt)).scalars().all()
        attacker = next((a for a in armies if a.army_id == attacker_id), None)
        defender = next((a for a in armies if a.army_id == defender_id), None)

        if not attacker or not defender:
            return None, "Armies not found.", None
        if attacker.army_type != defender.army_type:
            return None, "Cannot mix land and sea.", None

        battle_type = self._get_field_battle_type(attacker, defender)
        odds, _, _, _, _ = await self._calculate_field_battle_odds(
            attacker, defender, battle_type, ambush=ambush, defense=defense
        )

        new_battle = Battle(
            game_id=game_id,
            attacker_id=attacker.army_id,
            defender_id=defender.army_id,
            current_odds=odds,
            battle_type=battle_type,
            att_start_count=attacker.troop_count,
            def_start_count=defender.troop_count,
            # Ensure cargo snapshots are taken for auto-battles too
            att_start_cargo_count=(
                attacker.cargo.get("troop_count", 0) if attacker.cargo else 0
            ),
            def_start_cargo_count=(
                defender.cargo.get("troop_count", 0) if defender.cargo else 0
            ),
        )
        self.session.add(new_battle)
        self._stop_movement_immediately(attacker)
        self._stop_movement_immediately(defender)
        await self.session.commit()

        stmt_reload = (
            select(Battle)
            .where(Battle.id == new_battle.id)
            .options(
                selectinload(Battle.game),
                selectinload(Battle.attacker).selectinload(Army.house),
                selectinload(Battle.defender).selectinload(Army.house),
            )
        )
        reloaded_battle = (await self.session.execute(stmt_reload)).scalars().first()

        return reloaded_battle, f"Attacker Odds: 1 - {odds}", "Auto-battle started."

    async def process_auto_battle_round(self, battle_id: int):
        """
        Auto-Battle round processing. Returns 4-element tuple for Celery.
        """
        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(
                selectinload(Battle.game),
                # --- FIX: Add these to prevent MissingGreenlet in tasks ---
                selectinload(Battle.attacker).selectinload(Army.house),
                selectinload(Battle.defender).selectinload(Army.house)
            )
        )
        battle = (await self.session.execute(stmt)).scalars().first()
        if not battle:
            return None, "Battle not found.", None, None

        if battle.attacker_score >= 5 or battle.defender_score >= 5:
            winner = (
                "Attacker"
                if battle.attacker_score > battle.defender_score
                else "Defender"
            )
            return battle, "Battle has already concluded.", winner, False

        stmt_armies = select(Army).where(
            Army.army_id.in_([battle.attacker_id, battle.defender_id])
        )
        armies = (await self.session.execute(stmt_armies)).scalars().all()
        attacker_army = next(
            (a for a in armies if a.army_id == battle.attacker_id), None
        )
        defender_army = next(
            (a for a in armies if a.army_id == battle.defender_id), None
        )
        if not attacker_army or not defender_army:
            return battle, "One or both armies missing", None, False

        odds_at_start = battle.current_odds
        roll = random.randint(1, 100)
        is_attacker_win = roll <= odds_at_start

        if is_attacker_win:
            battle.attacker_score += 1
            battle.current_odds = min(95, battle.current_odds + 5)
        else:
            battle.defender_score += 1
            battle.current_odds = max(5, battle.current_odds - 5)

        att_loss_pct = 0
        def_loss_pct = 0
        if is_attacker_win:
            def_loss_pct += 0
        else:
            att_loss_pct += 0

        att_losses = int(attacker_army.troop_count * att_loss_pct)
        def_losses = int(defender_army.troop_count * def_loss_pct)
        attacker_army.troop_count = max(0, attacker_army.troop_count - att_losses)
        defender_army.troop_count = max(0, defender_army.troop_count - def_losses)
        self._scale_composition(attacker_army, attacker_army.troop_count)
        self._scale_composition(defender_army, defender_army.troop_count)

        def apply_cargo_damage(army, loss_pct):
            if (
                army.army_type == "SEA"
                and army.cargo
                and army.cargo.get("troop_count", 0) > 0
            ):
                c_men = army.cargo["troop_count"]
                c_losses = int(c_men * loss_pct)
                c_new = max(0, c_men - c_losses)
                if c_losses > 0:
                    c_comp, _ = ArmyRepo._calculate_split(
                        army.cargo.get("composition", {}), c_new, c_men
                    )
                    new_cargo = dict(army.cargo)
                    new_cargo["troop_count"] = c_new
                    new_cargo["composition"] = c_comp
                    army.cargo = new_cargo
                    flag_modified(army, "cargo")

        apply_cargo_damage(attacker_army, att_loss_pct)
        apply_cargo_damage(defender_army, def_loss_pct)
        await self.session.commit()

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

    async def resolve_auto_battle_aftermath(
        self, battle_id: int
    ) -> tuple[str, int] | tuple[None, None]:
        # Reuse the manual logic since the atomic fix is needed here too
        return await self.resolve_manual_battle_aftermath(battle_id)

    async def start_siege(self, game_id, attacker_id, fief_name, defense_bonus_str):
        stmt_att = (
            select(Army)
            .where(Army.army_id == attacker_id)
            .options(selectinload(Army.house).selectinload(House.characters))
        )
        attacker = (await self.session.execute(stmt_att)).scalars().first()
        stmt_f = select(Fief).where(Fief.game_id == game_id, Fief.name.ilike(fief_name))
        fief = (await self.session.execute(stmt_f)).scalars().first()

        if not attacker or not fief:
            return None, "❌ Target not found.", None

        if attacker.army_type != "LAND":
            return None, "❌ Only land armies can start a siege.", None

        stmt_d = (
            select(Army)
            .where(
                Army.house_id == fief.owner_id,
                Army.location_x == fief.location_x,
                Army.location_y == fief.location_y,
                Army.status == "GARRISONED",
                Army.army_type == "LAND",
            )
            .options(selectinload(Army.house).selectinload(House.characters))
        )
        defender = (await self.session.execute(stmt_d)).scalars().first()

        if not defender:
            return None, "❌ No Land Garrison found.", None

        _, att_bp = self._calculate_army_bp(attacker)
        _, def_bp = self._calculate_army_bp(defender)
        att_mar = await self._get_army_martial(attacker)
        def_mar = await self._get_army_martial(defender)
        def_bonus = {"major": 20, "significant": 10, "minor": 5, "siege_camp": 3}.get(
            defense_bonus_str.lower(), 0
        )

        odds = 50 + ((att_bp + (att_mar / 3)) - (def_bp + (def_mar / 3) + def_bonus))

        new_battle = Battle(
            game_id=game_id,
            attacker_id=attacker.army_id,
            defender_id=defender.army_id,
            battle_type="SIEGE",
            siege_phase="WALLS",
            fief_id=fief.fief_id,
            current_odds=int(max(10, min(90, odds))),
            # Store initial counts for accurate reporting
            att_start_count=attacker.troop_count,
            def_start_count=defender.troop_count,
        )
        self.session.add(new_battle)
        self._stop_movement_immediately(attacker)
        await self.session.commit()

        stmt_reload = (
            select(Battle)
            .where(Battle.id == new_battle.id)
            .options(
                selectinload(Battle.game),
                selectinload(Battle.fief),
                selectinload(Battle.attacker).selectinload(Army.house),
                selectinload(Battle.defender).selectinload(Army.house),
            )
        )
        reloaded = (await self.session.execute(stmt_reload)).scalars().first()

        calc_log = (
            f"**Attacker:** BP `{att_bp:.1f}` + Martial `{att_mar}`\n"
            f"**Defender:** BP `{def_bp:.1f}` + Martial `{def_mar}` + Bonus `{def_bonus}`"
        )
        return reloaded, f"Odds: {int(max(10, min(90, odds)))}", calc_log

    async def resolve_siege_consequences(self, battle_id: int):
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
        if not battle:
            return (
                False,
                f"Battle ID {battle_id} not found. It may have been completed or deleted already.",
            )

        attacker_army = battle.attacker
        defender_army = battle.defender

        if battle.attacker_score >= battle.defender_score:
            # --- ATTACKER WINS ---
            winner, loser, fief = attacker_army, defender_army, battle.fief
            if not winner or not fief:
                await self.session.delete(battle)
                await self.session.commit()
                return (False, "Attacker or Fief data missing from battle record.")

            loot = fief.treasury or 0
            victim_house = await self.session.get(House, fief.owner_id)
            winner.treasury = (winner.treasury or 0) + loot
            fief.treasury = 0

            fief.owner_id = winner.house_id
            fief.integration = 0.10

            winner.location_x, winner.location_y = fief.location_x, fief.location_y
            winner.status = "GARRISONED"
            winner.commander_name = f"Garrison of {fief.name}"
            self._stop_movement_immediately(winner)

            if victim_house:
                stmt_assets = select(Army).where(
                    Army.house_id == victim_house.house_id,
                    Army.location_x == fief.location_x,
                    Army.location_y == fief.location_y,
                    Army.army_id != (loser.army_id if loser else 0),
                )
                assets = (await self.session.execute(stmt_assets)).scalars().all()
                for asset in assets:
                    asset.house_id = winner.house_id
                    asset.status = (
                        "DOCKED" if asset.army_type == "SEA" else "GARRISONED"
                    )
                    self._stop_movement_immediately(asset)

            # --- DB CLEANUP ---
            if loser:
                # 1. Delete Pending Interactions (Unlocks Army from Interactions)
                await self.session.execute(
                    delete(PendingInteraction).where(
                        or_(
                            PendingInteraction.army1_id == loser.army_id,
                            PendingInteraction.army2_id == loser.army_id,
                        )
                    )
                )

                # 2. Delete ALL Battle references (Unlocks Army from Battles)
                # This fixes the specific error you just encountered.
                # If the army is destroyed, it cannot be in ANY battle.
                await self.session.execute(
                    delete(Battle).where(
                        or_(
                            Battle.attacker_id == loser.army_id,
                            Battle.defender_id == loser.army_id,
                        )
                    )
                )

            # 3. Delete the specific battle object (if not caught by step 2)
            # We check if it's still attached to the session before deleting to avoid warnings
            if battle in self.session:
                await self.session.delete(battle)

            # 4. Delete Army (Safe now)
            if loser:
                await self.session.delete(loser)

            await self.session.commit()
            return (
                True,
                f"🏰 **Siege Victory!**\n**{winner.house.name}** has captured **{fief.name}**!",
            )
        else:
            # --- DEFENDER WINS ---
            loser = attacker_army

            # --- DB CLEANUP ---
            if loser and loser.troop_count <= 0:
                # 1. Delete Pending Interactions
                await self.session.execute(
                    delete(PendingInteraction).where(
                        or_(
                            PendingInteraction.army1_id == loser.army_id,
                            PendingInteraction.army2_id == loser.army_id,
                        )
                    )
                )

                # 2. Delete ALL Battle references for the destroyed loser
                await self.session.execute(
                    delete(Battle).where(
                        or_(
                            Battle.attacker_id == loser.army_id,
                            Battle.defender_id == loser.army_id,
                        )
                    )
                )

                # 3. Delete the Army
                await self.session.delete(loser)

            # Ensure the current battle is deleted (if it wasn't the loser's only battle)
            if battle in self.session:
                await self.session.delete(battle)

            await self.session.commit()
            return (
                True,
                f"🛡️ **Siege Repelled!**\nThe defenders of **{battle.fief.name}** held the walls. The siege is broken.",
            )

    async def manual_casualty_calculation(
        self, winner_id: int, loser_id: int, score_str: str, retreat_success: bool
    ):
        """
        GM Tool: Applies casualties based on a raw score string (e.g., "5-0", "3-2").
        """
        # 1. Fetch Armies
        stmt = (
            select(Army)
            .where(Army.army_id.in_([winner_id, loser_id]))
            .options(selectinload(Army.house))
        )
        armies = (await self.session.execute(stmt)).scalars().all()
        winner = next((a for a in armies if a.army_id == winner_id), None)
        loser = next((a for a in armies if a.army_id == loser_id), None)

        if not winner or not loser:
            return False, "One or both armies not found."

        # 2. Parse Score
        try:
            # "5-0" -> [5, 0] -> loser gets 0
            parts = score_str.replace("-", " ").split()
            scores = [int(p) for p in parts]
            loser_score_val = min(scores)
        except:
            return False, "Invalid score format. Use '5-0' or '3-2'."

        # 3. Determine Casualty Percentages
        # Index Logic: 5-0 score = 5 (Massacre). 5-4 score = 1 (Close).
        severity_index = max(0, min(5, 5 - loser_score_val))

        # FIX: Use the Global tables defined at the top of the file
        # This ensures 5-0 results in 90% loss (index 5), not 15%.
        is_sea = winner.army_type == "SEA"

        if is_sea:
            win_pct = SEA_WINNER_CASUALTY_TABLE.get(severity_index, 0.04)
            los_pct = SEA_LOSER_CASUALTY_TABLE.get(severity_index, 0.32)
        else:
            win_pct = WINNER_CASUALTY_TABLE.get(severity_index, 0.05)
            los_pct = LOSER_CASUALTY_TABLE.get(severity_index, 0.50)

        # 4. Apply Casualties Helper
        from sqlalchemy.orm.attributes import flag_modified

        def apply_loss(army, pct):
            if army.troop_count <= 0:
                return 0
            loss = int(army.troop_count * pct)
            if loss == 0 and army.troop_count > 0:
                loss = 1

            # Update troops
            army.troop_count = max(0, army.troop_count - loss)

            # Update composition
            if army.composition:
                current_total = sum(army.composition.values())
                if current_total > 0:
                    ratio = army.troop_count / current_total
                    new_comp = {k: int(v * ratio) for k, v in army.composition.items()}
                    army.composition = new_comp
                    flag_modified(army, "composition")

            # Update Cargo (If fleet)
            if (
                army.army_type == "SEA"
                and army.cargo
                and army.cargo.get("troop_count", 0) > 0
            ):
                c_count = army.cargo["troop_count"]
                c_loss = int(c_count * pct)
                army.cargo["troop_count"] = max(0, c_count - c_loss)
                flag_modified(army, "cargo")

            return loss

        w_losses = apply_loss(winner, win_pct)
        l_losses = apply_loss(loser, los_pct)

        # 5. Handle Retreat vs Destruction
        fate_msg = ""
        if not retreat_success:
            # Army Destroyed
            fate_msg = f"\n💀 **{loser.commander_name}** was unable to retreat and the army has been **Destroyed**."

            # Clean up pending interactions for the destroyed army
            await self.session.execute(
                delete(PendingInteraction).where(
                    or_(
                        PendingInteraction.army1_id == loser.army_id,
                        PendingInteraction.army2_id == loser.army_id,
                    )
                )
            )

            await self.session.delete(loser)
        else:
            # Army Retreats
            fate_msg = (
                f"\n🏳️ **{loser.commander_name}** has **Retreated** from the field."
            )
            loser.status = "RETREATING"

        winner.status = "IDLE"

        await self.session.commit()

        return True, (
            f"**Score:** {score_str}\n"
            f"**Winner:** {winner.commander_name} (Lost {w_losses})\n"
            f"**Loser:** {loser.commander_name} (Lost {l_losses})\n"
            f"{fate_msg}"
        )

    async def occupy_fief(
        self, game_id, user_id, army_id, is_gm_override=False, acting_house_id=None
    ):
        """
        Allows an army to instantly capture a Fief if it is undefended.
        UPDATED: Uses Fief Treasury logic and fixes asset loop indentation.
        """
        # 1. Validation
        army = await ArmyRepo.get_army_by_id(self.session, army_id)
        if not army:
            return False, "❌ Army not found."

        effective_commanding_house_id = (
            acting_house_id if is_gm_override and acting_house_id else army.house_id
        )
        if not is_gm_override:
            player = await self.session.scalar(
                select(GamePlayer).where(
                    GamePlayer.user_id == user_id, GamePlayer.game_id == game_id
                )
            )
            if not player or not player.claimed_house_id:
                return False, "❌ No house."
            effective_commanding_house_id = player.claimed_house_id

        if army.house_id != effective_commanding_house_id:
            return False, "❌ Not your army."
        if army.army_type != "LAND":
            return False, "❌ Land armies only."

        # 2. Check Location
        stmt_fief = select(Fief).where(
            Fief.game_id == game_id,
            Fief.location_x == army.location_x,
            Fief.location_y == army.location_y,
        )
        fief = (await self.session.execute(stmt_fief)).scalars().first()

        if not fief:
            return False, "❌ There is no Fief at this location."

        if fief.owner_id == effective_commanding_house_id:
            army.status = "GARRISONED"
            army.commander_name = f"Garrison of {fief.name}"
            self._stop_movement_immediately(army)
            await self.session.commit()
            return True, f"✅ **{army.commander_name}** has garrisoned {fief.name}."

        # 3. Check for Defenders
        stmt_def = select(Army).where(
            Army.house_id == fief.owner_id,
            Army.location_x == fief.location_x,
            Army.location_y == fief.location_y,
            Army.status == "GARRISONED",
            Army.army_type == "LAND",
        )
        defender = (await self.session.execute(stmt_def)).scalars().first()
        if defender:
            return False, f"❌ Defended! Use siege."

        # 4. EXECUTE CONQUEST

        # --- LOOT LOGIC (Fief -> Army) ---
        loot = fief.treasury or 0

        # Add to the Army's "Wallet"
        army.treasury = (army.treasury or 0) + loot

        # Empty the Fief
        fief.treasury = 0
        # ---------------------------------

        # Save old owner ID to find their assets later
        old_owner_id = fief.owner_id

        # Transfer Fief
        fief.owner_id = effective_commanding_house_id
        fief.integration = 0.10

        # Garrison Attacker
        army.status = "GARRISONED"
        army.commander_name = f"Garrison of {fief.name}"
        self._stop_movement_immediately(army)

        # 5. Asset Seizure
        stmt_assets = select(Army).where(
            Army.house_id == old_owner_id,
            Army.location_x == fief.location_x,
            Army.location_y == fief.location_y,
            Army.army_id != army.army_id,
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

            # FIX: Indented INSIDE the loop
            self._stop_movement_immediately(asset)

        await self.session.commit()

        return True, (
            f"🏰 **{fief.name} Occupied!**\n"
            f"Since there was no garrison, your forces marched right in.\n"
            f"💰 **Loot Seized:** {loot} Gold (Added to Army)\n"
            f"📉 Integration reset to **10%**.{captured_text}"
        )
