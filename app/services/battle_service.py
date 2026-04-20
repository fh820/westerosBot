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
FIELD_PHASES = ("SKIRMISH", "MANEUVER", "CLASH", "PRESS", "ROUT")
FIELD_PHASE_RULES = {
    "SKIRMISH": {
        "winner_loss": 0.015,
        "loser_loss": 0.030,
        "winner_morale": 2,
        "loser_morale": 6,
        "odds_shift": 5,
    },
    "MANEUVER": {
        "winner_loss": 0.020,
        "loser_loss": 0.050,
        "winner_morale": 3,
        "loser_morale": 8,
        "odds_shift": 5,
    },
    "CLASH": {
        "winner_loss": 0.040,
        "loser_loss": 0.080,
        "winner_morale": 5,
        "loser_morale": 12,
        "odds_shift": 8,
    },
    "PRESS": {
        "winner_loss": 0.030,
        "loser_loss": 0.070,
        "winner_morale": 4,
        "loser_morale": 10,
        "odds_shift": 6,
    },
    "ROUT": {
        "winner_loss": 0.020,
        "loser_loss": 0.100,
        "winner_morale": 3,
        "loser_morale": 16,
        "odds_shift": 0,
    },
}
ATTACKER_SIEGE_ACTIONS = {
    "invest": {
        "wall": 0,
        "def_supply": -10,
        "att_supply": -3,
        "def_morale": -4,
        "att_morale": -1,
    },
    "bombard": {
        "wall": -12,
        "def_supply": -5,
        "att_supply": -8,
        "def_morale": -7,
        "att_morale": -2,
    },
    "mine": {
        "wall": -16,
        "def_supply": -3,
        "att_supply": -10,
        "def_morale": -6,
        "att_morale": -3,
    },
    "assault": {
        "wall": -8,
        "def_supply": -2,
        "att_supply": -6,
        "def_morale": -12,
        "att_morale": -8,
    },
    "raid": {
        "wall": 0,
        "def_supply": -8,
        "att_supply": 6,
        "def_morale": -5,
        "att_morale": 2,
    },
}
DEFENDER_SIEGE_ACTIONS = {
    "repair": {"wall": 10, "def_supply": -4, "def_morale": 2},
    "sally": {
        "wall": 0,
        "def_supply": -8,
        "att_supply": -10,
        "att_morale": -7,
        "def_morale": -4,
    },
    "ration": {"wall": 0, "def_supply": 5, "def_morale": -4},
    "counter_mine": {"wall": 8, "def_supply": -5, "def_morale": 1},
    "ambush": {"wall": 0, "def_supply": -4, "att_morale": -8, "def_morale": 2},
}
SIEGE_DEFAULT_ATTACKER_ACTION = "invest"
SIEGE_DEFAULT_DEFENDER_ACTION = "ration"
FIELD_PLANS = {
    "aggressive",
    "defensive",
    "flank",
    "feint",
    "cautious",
    "ambush",
    "reserve",
}
FIELD_TERRAINS = {
    "unknown",
    "plains",
    "hills",
    "forest",
    "mountains",
    "river",
    "marsh",
    "urban",
    "coast",
    "open_sea",
    "strait",
    "storm",
}
FIELD_PLAN_MATCHUPS = {
    ("aggressive", "defensive"): -10,
    ("aggressive", "feint"): 6,
    ("aggressive", "cautious"): 4,
    ("defensive", "aggressive"): 8,
    ("defensive", "flank"): -6,
    ("flank", "defensive"): 8,
    ("flank", "cautious"): -5,
    ("feint", "aggressive"): 9,
    ("feint", "defensive"): -4,
    ("cautious", "ambush"): 10,
    ("cautious", "feint"): -5,
    ("ambush", "aggressive"): 12,
    ("ambush", "cautious"): -10,
    ("reserve", "aggressive"): 5,
    ("reserve", "flank"): 4,
}
FIELD_PHASE_PLAN_MODIFIERS = {
    "SKIRMISH": {"ambush": 8, "cautious": 3, "feint": 4, "aggressive": -2},
    "MANEUVER": {"feint": 5, "flank": 4, "cautious": 2, "reserve": 2},
    "CLASH": {"aggressive": 6, "defensive": 3, "reserve": 4, "flank": 5},
    "PRESS": {"aggressive": 5, "flank": 5, "reserve": 3, "defensive": -2},
    "ROUT": {"flank": 6, "aggressive": 4, "cautious": -4, "reserve": 3},
}


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

    def _clamp(self, value, min_value, max_value):
        return max(min_value, min(max_value, value))

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
        breakdown = await self._calculate_field_battle_odds_breakdown(
            attacker,
            defender,
            battle_type,
            ambush=ambush,
            defense=defense,
            att_bonus_override=att_bonus_override,
            def_bonus_override=def_bonus_override,
            att_cmd_override=att_cmd_override,
            def_cmd_override=def_cmd_override,
            score_diff=score_diff,
        )
        return (
            breakdown["final_odds"],
            breakdown["att_bp"],
            breakdown["def_bp"],
            breakdown["att_bonus_total"],
            breakdown["def_bonus_total"],
        )

    async def _calculate_field_battle_odds_breakdown(
        self,
        attacker: Army,
        defender: Army,
        battle_type: str,
        ambush=None,
        defense=None,
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

        att_commander_bonus = att_martial / 3.0
        def_commander_bonus = def_martial / 3.0
        att_ambush_bonus = 0
        def_defense_bonus = 0
        att_outnumber_bonus = 0
        def_outnumber_bonus = 0

        if battle_type == "LAND_BATTLE":
            att_ambush_bonus = LAND_AMBUSH_BONUSES.get(
                (ambush or "none").lower(), 0
            )
            def_defense_bonus = LAND_DEFENSE_BONUSES.get(
                (defense or "none").lower(), 0
            )

        if attacker.troop_count > defender.troop_count * BATTLE_OUTNUMBER_THRESHOLD:
            att_outnumber_bonus = BATTLE_OUTNUMBER_BONUS
        elif defender.troop_count > attacker.troop_count * BATTLE_OUTNUMBER_THRESHOLD:
            def_outnumber_bonus = BATTLE_OUTNUMBER_BONUS

        att_bonus = (
            att_commander_bonus
            + att_bonus_override
            + att_ambush_bonus
            + att_outnumber_bonus
        )
        def_bonus = (
            def_commander_bonus
            + def_bonus_override
            + def_defense_bonus
            + def_outnumber_bonus
        )

        att_total = att_bp + att_bonus
        def_total = def_bp + def_bonus
        base_odds = self._odds_from_totals(att_total, def_total)
        momentum = score_diff * BATTLE_MOMENTUM_PER_SCORE
        final_odds = int(
            max(
                BATTLE_ODDS_MIN,
                min(BATTLE_ODDS_MAX, base_odds + momentum),
            )
        )
        return {
            "battle_type": battle_type,
            "att_bp": att_bp,
            "def_bp": def_bp,
            "att_martial": att_martial,
            "def_martial": def_martial,
            "att_commander_bonus": att_commander_bonus,
            "def_commander_bonus": def_commander_bonus,
            "att_bonus_override": att_bonus_override,
            "def_bonus_override": def_bonus_override,
            "att_ambush_bonus": att_ambush_bonus,
            "def_defense_bonus": def_defense_bonus,
            "att_outnumber_bonus": att_outnumber_bonus,
            "def_outnumber_bonus": def_outnumber_bonus,
            "att_bonus_total": att_bonus,
            "def_bonus_total": def_bonus,
            "att_total": att_total,
            "def_total": def_total,
            "base_odds": base_odds,
            "momentum": momentum,
            "final_odds": final_odds,
        }

    def _format_composition_for_gm(self, army: Army):
        if not army or not army.composition:
            return "none"
        parts = []
        for unit, count in sorted(army.composition.items()):
            parts.append(f"{unit} {count}")
        return ", ".join(parts)

    def _format_field_odds_breakdown(self, battle, attacker, defender, breakdown):
        return (
            f"**Battle Type:** `{breakdown['battle_type']}`\n"
            f"**Terrain:** `{getattr(battle, 'terrain', None) or 'unknown'}`\n"
            f"**Phase:** `{getattr(battle, 'phase', None) or 'SKIRMISH'}` | "
            f"**Round:** `{getattr(battle, 'round_number', 0) or 0}`\n"
            f"**Plans:** Attacker `{getattr(battle, 'attacker_plan', None) or 'cautious'}` / "
            f"Defender `{getattr(battle, 'defender_plan', None) or 'cautious'}`\n"
            f"**Morale:** Attacker `{getattr(battle, 'attacker_morale', 100) or 100}` / "
            f"Defender `{getattr(battle, 'defender_morale', 100) or 100}`\n"
            f"**Supply:** Attacker `{getattr(battle, 'attacker_supply', 100) or 100}` / "
            f"Defender `{getattr(battle, 'defender_supply', 100) or 100}`\n\n"
            f"**Attacker Units:** `{attacker.troop_count}` "
            f"({self._format_composition_for_gm(attacker)})\n"
            f"Unit BP `{breakdown['att_bp']:.2f}` + Commander "
            f"`{breakdown['att_martial']}`/3 = `{breakdown['att_commander_bonus']:.2f}` + "
            f"Manual `{breakdown['att_bonus_override']:+.2f}` + "
            f"Ambush `{breakdown['att_ambush_bonus']:+.2f}` + "
            f"Outnumber `{breakdown['att_outnumber_bonus']:+.2f}` = "
            f"Bonus `{breakdown['att_bonus_total']:.2f}`\n"
            f"Attacker Total: `{breakdown['att_total']:.2f}`\n\n"
            f"**Defender Units:** `{defender.troop_count}` "
            f"({self._format_composition_for_gm(defender)})\n"
            f"Unit BP `{breakdown['def_bp']:.2f}` + Commander "
            f"`{breakdown['def_martial']}`/3 = `{breakdown['def_commander_bonus']:.2f}` + "
            f"Manual `{breakdown['def_bonus_override']:+.2f}` + "
            f"Defense `{breakdown['def_defense_bonus']:+.2f}` + "
            f"Outnumber `{breakdown['def_outnumber_bonus']:+.2f}` = "
            f"Bonus `{breakdown['def_bonus_total']:.2f}`\n"
            f"Defender Total: `{breakdown['def_total']:.2f}`\n\n"
            f"**Baseline Odds:** attacker `{breakdown['base_odds']}` / defender "
            f"`{100 - breakdown['base_odds']}`\n"
            f"**Momentum Adjustment:** `{breakdown['momentum']:+}`\n"
            f"**Starting Current Odds:** attacker `1-{breakdown['final_odds']}` / "
            f"defender `{breakdown['final_odds'] + 1}-100`\n\n"
            f"Note: phase resolution also applies current morale, supply, terrain, "
            f"and plan matchup adjustments before the roll."
        )

    def _next_field_phase(self, current_phase):
        try:
            index = FIELD_PHASES.index((current_phase or "SKIRMISH").upper())
        except ValueError:
            return "SKIRMISH"
        next_index = index + 1
        return FIELD_PHASES[next_index] if next_index < len(FIELD_PHASES) else "COMPLETE"

    def _apply_phase_losses(self, army, loss_pct):
        if not army or army.troop_count <= 0 or loss_pct <= 0:
            return 0

        losses = int(army.troop_count * loss_pct)
        if losses == 0 and army.troop_count > 0:
            losses = 1
        losses = min(losses, army.troop_count)

        if (
            army.army_type == "SEA"
            and army.cargo
            and army.cargo.get("troop_count", 0) > 0
        ):
            initial_ships = army.troop_count
            survival_rate = (
                (initial_ships - losses) / initial_ships if initial_ships > 0 else 0
            )
            old_cargo_count = army.cargo["troop_count"]
            new_cargo_count = int(old_cargo_count * survival_rate)
            if new_cargo_count < old_cargo_count:
                c_comp, _ = ArmyRepo._calculate_split(
                    army.cargo.get("composition", {}),
                    new_cargo_count,
                    old_cargo_count,
                )
                new_cargo = dict(army.cargo)
                new_cargo["troop_count"] = new_cargo_count
                new_cargo["composition"] = c_comp
                army.cargo = new_cargo
                flag_modified(army, "cargo")

        army.troop_count = max(0, army.troop_count - losses)
        self._scale_composition(army, army.troop_count)
        return losses

    def _field_phase_winner(self, battle):
        att_morale = battle.attacker_morale or 0
        def_morale = battle.defender_morale or 0

        if att_morale <= 25 and def_morale > att_morale:
            return "Defender"
        if def_morale <= 25 and att_morale > def_morale:
            return "Attacker"
        if (battle.phase or "").upper() != "COMPLETE":
            return None
        if battle.attacker_score > battle.defender_score:
            return "Attacker"
        if battle.defender_score > battle.attacker_score:
            return "Defender"
        return "Attacker" if att_morale >= def_morale else "Defender"

    def _composition_count(self, army, *unit_names):
        if not army or not army.composition:
            return 0
        wanted = {name.lower() for name in unit_names}
        return sum(
            count
            for unit, count in army.composition.items()
            if unit.lower() in wanted
        )

    def _terrain_army_modifier(self, army, terrain, phase, is_attacker):
        if not army or army.army_type == "SEA":
            return 0

        terrain = (terrain or "unknown").lower()
        phase = (phase or "SKIRMISH").upper()
        total = max(army.troop_count or 0, 1)
        cavalry_ratio = self._composition_count(army, "knights", "cavalry") / total
        archer_ratio = self._composition_count(army, "archers") / total
        militia_ratio = self._composition_count(army, "militia") / total
        infantry_ratio = self._composition_count(army, "infantry") / total
        modifier = 0

        if terrain == "plains":
            if phase in ("CLASH", "ROUT"):
                modifier += int(cavalry_ratio * 18)
        elif terrain == "hills":
            if phase == "SKIRMISH":
                modifier += int(archer_ratio * 12)
            if phase == "CLASH":
                modifier += int(infantry_ratio * 4)
                modifier -= int(cavalry_ratio * 5)
        elif terrain == "forest":
            modifier += int((archer_ratio + militia_ratio) * 8)
            modifier -= int(cavalry_ratio * 14)
        elif terrain == "mountains":
            modifier += int((archer_ratio + infantry_ratio) * 6)
            modifier -= int(cavalry_ratio * 16)
        elif terrain == "river":
            if is_attacker and phase == "CLASH":
                modifier -= 14
            if not is_attacker:
                modifier += 10
            modifier += int(archer_ratio * 5)
        elif terrain == "marsh":
            modifier += int(militia_ratio * 8)
            modifier -= int(cavalry_ratio * 18)
            if phase == "ROUT":
                modifier -= 4
        elif terrain == "urban":
            modifier += int((infantry_ratio + militia_ratio) * 8)
            modifier -= int(cavalry_ratio * 12)
            if phase == "ROUT":
                modifier -= 5
        elif terrain == "coast":
            modifier += int(infantry_ratio * 3)

        return modifier

    def _naval_terrain_modifier(self, terrain, phase):
        terrain = (terrain or "unknown").lower()
        phase = (phase or "SKIRMISH").upper()
        if terrain == "open_sea":
            return 2
        if terrain == "coast":
            return 1 if phase != "ROUT" else -2
        if terrain == "strait":
            return -4 if phase == "SKIRMISH" else 3
        if terrain == "storm":
            return -8
        return 0

    def _field_plan_modifier(self, battle, phase):
        att_plan = (battle.attacker_plan or "cautious").lower()
        def_plan = (battle.defender_plan or "cautious").lower()
        phase = (phase or "SKIRMISH").upper()

        modifier = FIELD_PLAN_MATCHUPS.get((att_plan, def_plan), 0)
        phase_mods = FIELD_PHASE_PLAN_MODIFIERS.get(phase, {})
        modifier += phase_mods.get(att_plan, 0)
        modifier -= phase_mods.get(def_plan, 0)
        return modifier

    def _field_context_modifier(self, battle, attacker_army, defender_army, phase):
        terrain = (battle.terrain or "unknown").lower()
        plan_modifier = self._field_plan_modifier(battle, phase)

        if attacker_army.army_type == "SEA":
            terrain_modifier = self._naval_terrain_modifier(terrain, phase)
            return plan_modifier + terrain_modifier, plan_modifier, terrain_modifier

        att_terrain = self._terrain_army_modifier(
            attacker_army, terrain, phase, is_attacker=True
        )
        def_terrain = self._terrain_army_modifier(
            defender_army, terrain, phase, is_attacker=False
        )
        terrain_modifier = att_terrain - def_terrain
        return plan_modifier + terrain_modifier, plan_modifier, terrain_modifier

    def _format_phase_odds_audit(
        self,
        battle,
        phase,
        base_odds,
        morale_adjustment,
        supply_adjustment,
        plan_modifier,
        terrain_modifier,
        phase_odds,
        roll,
        is_attacker_win,
        attacker_army,
        defender_army,
    ):
        return (
            f"**Phase:** `{phase}` | **Round:** `{battle.round_number or 0}`\n"
            f"**Terrain:** `{battle.terrain or 'unknown'}`\n"
            f"**Plans:** Attacker `{battle.attacker_plan or 'cautious'}` / "
            f"Defender `{battle.defender_plan or 'cautious'}`\n"
            f"**Forces:** Attacker `{attacker_army.troop_count}` / "
            f"Defender `{defender_army.troop_count}`\n"
            f"**Morale:** Attacker `{battle.attacker_morale}` / "
            f"Defender `{battle.defender_morale}`\n"
            f"**Supply:** Attacker `{battle.attacker_supply}` / "
            f"Defender `{battle.defender_supply}`\n\n"
            f"Starting current odds: `{base_odds}`\n"
            f"Morale adjustment: `{morale_adjustment:+.2f}`\n"
            f"Supply adjustment: `{supply_adjustment:+.2f}`\n"
            f"Plan matchup/phase adjustment: `{plan_modifier:+}`\n"
            f"Terrain/composition adjustment: `{terrain_modifier:+}`\n"
            f"Final phase target: attacker `1-{phase_odds}` / "
            f"defender `{phase_odds + 1}-100`\n"
            f"Roll: `{roll}` -> **{'Attacker' if is_attacker_win else 'Defender'}** won the phase."
        )

    def _plan_loss_multiplier(self, plan, phase, won_phase):
        plan = (plan or "cautious").lower()
        phase = (phase or "SKIRMISH").upper()
        multiplier = 1.0

        if plan == "defensive":
            multiplier *= 0.85
        elif plan == "aggressive":
            multiplier *= 1.15 if not won_phase else 0.95
        elif plan == "cautious":
            multiplier *= 0.90
        elif plan == "ambush":
            multiplier *= 0.85 if won_phase and phase == "SKIRMISH" else 1.10
        elif plan == "flank":
            multiplier *= 0.90 if won_phase and phase == "ROUT" else 1.05
        elif plan == "reserve":
            multiplier *= 0.90 if phase in ("CLASH", "PRESS", "ROUT") else 1.0

        return multiplier

    async def set_field_plan(self, battle_id: int, side: str, plan: str):
        battle = await self.session.get(Battle, battle_id)
        if not battle or battle.battle_type not in ("LAND_BATTLE", "SEA_BATTLE"):
            return False, "Field battle not found."

        side_key = (side or "").lower()
        plan_key = (plan or "").lower().replace("-", "_")
        if plan_key not in FIELD_PLANS:
            return (
                False,
                "Unknown plan. Use aggressive, defensive, flank, feint, cautious, ambush, or reserve.",
            )

        if side_key in ("attacker", "attack", "att"):
            battle.attacker_plan = plan_key
        elif side_key in ("defender", "defense", "def"):
            battle.defender_plan = plan_key
        else:
            return False, "Side must be attacker or defender."

        await self.session.commit()
        return True, f"{side_key.title()} plan set to {plan_key}."

    async def set_battle_terrain(self, battle_id: int, terrain: str):
        battle = await self.session.get(Battle, battle_id)
        if not battle:
            return False, "Battle not found."

        terrain_key = (terrain or "").lower().replace("-", "_")
        if terrain_key == "plain":
            terrain_key = "plains"
        if terrain_key not in FIELD_TERRAINS:
            return (
                False,
                "Unknown terrain. Use plains, hills, forest, mountains, river, marsh, urban, coast, open_sea, strait, storm, or unknown.",
            )

        battle.terrain = terrain_key
        await self.session.commit()
        return True, f"Battle terrain set to {terrain_key}."

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

        breakdown = await self._calculate_field_battle_odds_breakdown(
            attacker,
            defender,
            battle_type,
            att_bonus_override=att_bonus,
            def_bonus_override=def_bonus,
            att_cmd_override=att_cmd_override,
            def_cmd_override=def_cmd_override,
            score_diff=battle.attacker_score - battle.defender_score,
        )
        battle.current_odds = breakdown["final_odds"]
        calc_log = self._format_field_odds_breakdown(battle, attacker, defender, breakdown)
        await self.session.commit()
        return battle, calc_log

    async def start_battle(
        self, game_id, attacker_id, defender_id, ambush, defense, terrain="unknown"
    ):
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
        terrain_key = (terrain or "unknown").lower().replace("-", "_")
        if terrain_key == "plain":
            terrain_key = "plains"
        if terrain_key not in FIELD_TERRAINS:
            terrain_key = "unknown"

        breakdown = await self._calculate_field_battle_odds_breakdown(
            attacker, defender, battle_type, ambush=ambush, defense=defense
        )
        odds = breakdown["final_odds"]

        new_battle = Battle(
            game_id=game_id,
            attacker_id=attacker.army_id,
            defender_id=defender.army_id,
            current_odds=odds,
            battle_type=battle_type,
            phase="SKIRMISH",
            round_number=0,
            terrain=terrain_key,
            attacker_morale=100,
            defender_morale=100,
            attacker_plan="cautious",
            defender_plan="cautious",
            attacker_supply=100,
            defender_supply=100,
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

        calc_log = self._format_field_odds_breakdown(
            reloaded_battle, attacker, defender, breakdown
        )
        return reloaded_battle, f"Attacker Odds: 1 - {odds}", calc_log

    async def process_field_battle_phase(self, battle_id: int):
        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(
                selectinload(Battle.game),
                selectinload(Battle.attacker).selectinload(Army.house),
                selectinload(Battle.defender).selectinload(Army.house),
            )
        )
        battle = (await self.session.execute(stmt)).scalars().first()
        if not battle:
            return None, "Battle not found.", None, False, None, None

        if battle.battle_type == "SIEGE":
            return None, "Not a field battle.", None, False, None, None

        if (battle.phase or "").upper() == "COMPLETE":
            winner = self._field_phase_winner(battle)
            return battle, "Battle already finished.", winner, False, "Battle is over.", {}

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

        phase = (battle.phase or "SKIRMISH").upper()
        if phase not in FIELD_PHASE_RULES:
            phase = "SKIRMISH"
            battle.phase = phase

        morale_adjustment = ((battle.attacker_morale or 100) - (battle.defender_morale or 100)) / 4
        supply_adjustment = ((battle.attacker_supply or 100) - (battle.defender_supply or 100)) / 8
        context_modifier, plan_modifier, terrain_modifier = self._field_context_modifier(
            battle, attacker_army, defender_army, phase
        )
        phase_odds = int(
            self._clamp(
                battle.current_odds
                + morale_adjustment
                + supply_adjustment
                + context_modifier,
                5,
                95,
            )
        )

        roll = random.randint(1, 100)
        is_attacker_win = roll <= phase_odds
        battle.round_number = (battle.round_number or 0) + 1
        gm_audit = self._format_phase_odds_audit(
            battle,
            phase,
            battle.current_odds,
            morale_adjustment,
            supply_adjustment,
            plan_modifier,
            terrain_modifier,
            phase_odds,
            roll,
            is_attacker_win,
            attacker_army,
            defender_army,
        )

        rules = FIELD_PHASE_RULES[phase]
        sea_multiplier = 0.8 if attacker_army.army_type == "SEA" else 1.0
        winner_loss_pct = rules["winner_loss"] * sea_multiplier
        loser_loss_pct = rules["loser_loss"] * sea_multiplier
        att_plan = (battle.attacker_plan or "cautious").lower()
        def_plan = (battle.defender_plan or "cautious").lower()

        if is_attacker_win:
            battle.attacker_score += 1
            battle.attacker_morale = int(
                self._clamp((battle.attacker_morale or 100) + rules["winner_morale"], 0, 100)
            )
            battle.defender_morale = int(
                self._clamp((battle.defender_morale or 100) - rules["loser_morale"], 0, 100)
            )
            att_losses = self._apply_phase_losses(
                attacker_army,
                winner_loss_pct * self._plan_loss_multiplier(att_plan, phase, True),
            )
            def_losses = self._apply_phase_losses(
                defender_army,
                loser_loss_pct * self._plan_loss_multiplier(def_plan, phase, False),
            )
            round_winner_name = attacker_army.commander_name
            battle.current_odds = int(
                self._clamp(battle.current_odds + rules["odds_shift"], 5, 95)
            )
        else:
            battle.defender_score += 1
            battle.defender_morale = int(
                self._clamp((battle.defender_morale or 100) + rules["winner_morale"], 0, 100)
            )
            battle.attacker_morale = int(
                self._clamp((battle.attacker_morale or 100) - rules["loser_morale"], 0, 100)
            )
            att_losses = self._apply_phase_losses(
                attacker_army,
                loser_loss_pct * self._plan_loss_multiplier(att_plan, phase, False),
            )
            def_losses = self._apply_phase_losses(
                defender_army,
                winner_loss_pct * self._plan_loss_multiplier(def_plan, phase, True),
            )
            round_winner_name = defender_army.commander_name
            battle.current_odds = int(
                self._clamp(battle.current_odds - rules["odds_shift"], 5, 95)
            )

        previous_phase = phase
        battle.phase = self._next_field_phase(phase)
        winner = self._field_phase_winner(battle)
        if winner:
            battle.phase = "COMPLETE"

        unit_term = "ships" if attacker_army.army_type == "SEA" else "troops"
        roll_msg = (
            f"Phase: **{previous_phase.title()}**\n"
            f"Roll: **{roll}**\n"
            f"Result: **{'Attacker' if is_attacker_win else 'Defender'}** won the phase.\n"
            f"Plans, morale, supply, and terrain all shaped the result."
        )
        narration = (
            f"**{round_winner_name}** seized the initiative in the "
            f"**{previous_phase.title()}**. The ground at **{battle.terrain or 'unknown'}** "
            f"and the chosen plans shaped the clash. The losing side's morale buckled, "
            f"and the field cost fresh {unit_term} before the line could steady."
        )

        await self.session.commit()
        return (
            battle,
            roll_msg,
            winner,
            False,
            narration,
            {"attacker": att_losses, "defender": def_losses, "_gm_audit": gm_audit},
        )

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
        if battle.battle_type in ("LAND_BATTLE", "SEA_BATTLE"):
            return await self.process_field_battle_phase(battle_id)
        if battle.battle_type == "SIEGE":
            return await self.process_siege_turn(battle_id)

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
        battle.round_number = (battle.round_number or 0) + 1

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
            battle.phase = "STREETS"
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

    async def fast_resolve_battle(self, battle_id: int, max_steps: int = 6):
        reports = []
        winner = None
        battle = None

        for _ in range(max_steps):
            battle, roll_msg, winner, phase_transition, narration, casualties = (
                await self.process_battle_round(battle_id)
            )
            if not battle:
                return None, reports, "Battle not found."
            if roll_msg == "Battle already finished.":
                break
            reports.append(
                {
                    "roll_msg": roll_msg,
                    "narration": narration,
                    "casualties": casualties or {},
                    "phase_transition": phase_transition,
                    "score": f"{battle.attacker_score}-{battle.defender_score}",
                    "phase": battle.phase or battle.siege_phase or "ROUND",
                }
            )
            if winner:
                break

        return battle, reports, winner

    async def set_siege_action(self, battle_id: int, side: str, action: str):
        battle = await self.session.get(Battle, battle_id)
        if not battle or battle.battle_type != "SIEGE":
            return False, "Siege not found."

        side_key = (side or "").lower()
        action_key = (action or "").lower().replace("-", "_")

        if side_key in ("attacker", "attack", "att"):
            if action_key not in ATTACKER_SIEGE_ACTIONS:
                return (
                    False,
                    "Unknown attacker action. Use invest, bombard, mine, assault, or raid.",
                )
            battle.attacker_plan = action_key
        elif side_key in ("defender", "defense", "def"):
            if action_key not in DEFENDER_SIEGE_ACTIONS:
                return (
                    False,
                    "Unknown defender action. Use repair, sally, ration, counter_mine, or ambush.",
                )
            battle.defender_plan = action_key
        else:
            return False, "Side must be attacker or defender."

        await self.session.commit()
        return True, f"Siege {side_key} action set to {action_key}."

    async def attach_blockade(self, battle_id: int, fleet_id: int):
        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(selectinload(Battle.fief), selectinload(Battle.attacker))
        )
        battle = (await self.session.execute(stmt)).scalars().first()
        fleet = await self.session.get(Army, fleet_id)

        if not battle or battle.battle_type != "SIEGE":
            return False, "Siege not found."
        if not fleet:
            return False, "Fleet not found."
        if fleet.army_type != "SEA":
            return False, "Only fleets can blockade a siege."
        if fleet.troop_count <= 0:
            return False, "Fleet has no ships."
        if not battle.fief:
            return False, "Siege has no target fief."
        if battle.attacker and fleet.house_id != battle.attacker.house_id:
            return False, "The blockading fleet must belong to the besieging house."

        distance = (
            ((fleet.location_x or 0) - (battle.fief.location_x or 0)) ** 2
            + ((fleet.location_y or 0) - (battle.fief.location_y or 0)) ** 2
        ) ** 0.5
        if distance > 150:
            return False, "Fleet is too far from the besieged fief to blockade it."

        stmt_fleets = select(Army).where(
            Army.game_id == battle.game_id,
            Army.army_type == "SEA",
            Army.army_id != fleet.army_id,
            Army.troop_count > 0,
            Army.location_x >= (battle.fief.location_x - 150),
            Army.location_x <= (battle.fief.location_x + 150),
            Army.location_y >= (battle.fief.location_y - 150),
            Army.location_y <= (battle.fief.location_y + 150),
        )
        nearby_fleets = (await self.session.execute(stmt_fleets)).scalars().all()
        hostile_screen = sum(
            other.troop_count
            for other in nearby_fleets
            if other.house_id != fleet.house_id
            and (
                ((other.location_x or 0) - (battle.fief.location_x or 0)) ** 2
                + ((other.location_y or 0) - (battle.fief.location_y or 0)) ** 2
            )
            ** 0.5
            <= 150
        )
        if hostile_screen >= max(1, int(fleet.troop_count * 0.75)):
            return False, "Hostile fleets contest the waters. Win naval superiority first."

        battle.blockade_fleet_id = fleet.army_id
        battle.defender_supply = int(self._clamp((battle.defender_supply or 100) - 5, 0, 100))
        await self.session.commit()
        return (
            True,
            f"Fleet {fleet.army_id} is now blockading siege {battle.id}. Defender supplies will fall faster.",
        )

    async def process_siege_turn(self, battle_id: int):
        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(
                selectinload(Battle.game),
                selectinload(Battle.fief),
                selectinload(Battle.attacker).selectinload(Army.house),
                selectinload(Battle.defender).selectinload(Army.house),
            )
        )
        battle = (await self.session.execute(stmt)).scalars().first()
        if not battle:
            return None, "Siege not found.", None, False, None, None
        if battle.battle_type != "SIEGE":
            return None, "Not a siege.", None, False, None, None
        if (battle.phase or "").upper() == "COMPLETE":
            winner = "Attacker" if battle.attacker_score >= battle.defender_score else "Defender"
            return battle, "Battle already finished.", winner, False, "Siege is over.", {}

        attacker_army = battle.attacker
        defender_army = battle.defender
        if not attacker_army or not defender_army:
            return battle, "Armies missing", None, False, "Error", {}

        if (battle.phase or "").upper() == "STREETS":
            odds = int(
                self._clamp(
                    battle.current_odds
                    + (((battle.attacker_morale or 100) - (battle.defender_morale or 100)) / 3)
                    + (((battle.attacker_supply or 100) - (battle.defender_supply or 100)) / 6),
                    10,
                    90,
                )
            )
            roll = random.randint(1, 100)
            attacker_wins = roll <= odds
            battle.round_number = (battle.round_number or 0) + 1

            if attacker_wins:
                att_losses = self._apply_phase_losses(attacker_army, 0.10)
                def_losses = self._apply_phase_losses(defender_army, 0.18)
                battle.attacker_score = 5
                battle.defender_score = 0
                battle.phase = "COMPLETE"
                battle.siege_phase = "STREETS"
                winner = "Attacker"
                narration = "The attackers forced their way through the breach and broke the last organized defense in the streets."
            else:
                att_losses = self._apply_phase_losses(attacker_army, 0.16)
                def_losses = self._apply_phase_losses(defender_army, 0.08)
                battle.attacker_score = 0
                battle.defender_score = 5
                battle.phase = "COMPLETE"
                winner = "Defender"
                narration = "The defenders turned the breach into a killing ground and threw the assault back."

            gm_audit = (
                f"**Siege Streets Resolution**\n"
                f"Starting street-fighting odds: attacker `1-{odds}` / "
                f"defender `{odds + 1}-100`\n"
                f"Roll: `{roll}` -> **{winner}** won the breach fight.\n"
                f"Morale: Attacker `{battle.attacker_morale}` / Defender `{battle.defender_morale}`\n"
                f"Supply: Attacker `{battle.attacker_supply}` / Defender `{battle.defender_supply}`"
            )
            await self.session.commit()
            return (
                battle,
                (
                    f"Streets\n"
                    f"Roll: **{roll}**\n"
                    f"Result: **{winner}** won the breach fight."
                ),
                winner,
                False,
                narration,
                {"attacker": att_losses, "defender": def_losses, "_gm_audit": gm_audit},
            )

        att_action = battle.attacker_plan or SIEGE_DEFAULT_ATTACKER_ACTION
        def_action = battle.defender_plan or SIEGE_DEFAULT_DEFENDER_ACTION
        old_wall = battle.wall_integrity if battle.wall_integrity is not None else 100
        old_att_supply = battle.attacker_supply or 100
        old_def_supply = battle.defender_supply or 100
        old_att_morale = battle.attacker_morale or 100
        old_def_morale = battle.defender_morale or 100
        att_effect = dict(ATTACKER_SIEGE_ACTIONS.get(att_action, ATTACKER_SIEGE_ACTIONS[SIEGE_DEFAULT_ATTACKER_ACTION]))
        def_effect = dict(DEFENDER_SIEGE_ACTIONS.get(def_action, DEFENDER_SIEGE_ACTIONS[SIEGE_DEFAULT_DEFENDER_ACTION]))
        special_notes = []

        if att_action == "mine" and def_action == "counter_mine":
            att_effect["wall"] = int(att_effect["wall"] / 3)
            def_effect["def_morale"] = def_effect.get("def_morale", 0) + 2
            special_notes.append("counter_mine reduced mine wall damage and added defender morale")
        if att_action == "bombard" and def_action == "repair":
            def_effect["wall"] = int(def_effect["wall"] / 2)
            special_notes.append("repair wall gain was halved under bombardment")
        if att_action == "assault" and def_action == "ambush":
            att_effect["att_morale"] -= 6
            special_notes.append("ambush added extra attacker morale damage during assault")

        blockade_text = ""
        if battle.blockade_fleet_id:
            att_effect["def_supply"] = att_effect.get("def_supply", 0) - 8
            att_effect["def_morale"] = att_effect.get("def_morale", 0) - 2
            blockade_text = "\nThe blockade tightened the noose around the port."
            special_notes.append("blockade applied defender supply and morale pressure")

        wall_random = random.randint(-3, 3)
        wall_delta = att_effect.get("wall", 0) + def_effect.get("wall", 0) + wall_random
        att_supply_delta = att_effect.get("att_supply", 0) + def_effect.get("att_supply", 0)
        def_supply_delta = att_effect.get("def_supply", 0) + def_effect.get("def_supply", 0)
        att_morale_delta = att_effect.get("att_morale", 0) + def_effect.get("att_morale", 0)
        def_morale_delta = att_effect.get("def_morale", 0) + def_effect.get("def_morale", 0)

        battle.wall_integrity = int(self._clamp((battle.wall_integrity if battle.wall_integrity is not None else 100) + wall_delta, 0, 120))
        battle.attacker_supply = int(self._clamp((battle.attacker_supply or 100) + att_supply_delta, 0, 100))
        battle.defender_supply = int(self._clamp((battle.defender_supply or 100) + def_supply_delta, 0, 100))
        battle.attacker_morale = int(self._clamp((battle.attacker_morale or 100) + att_morale_delta, 0, 100))
        battle.defender_morale = int(self._clamp((battle.defender_morale or 100) + def_morale_delta, 0, 100))
        battle.round_number = (battle.round_number or 0) + 1

        att_losses = 0
        def_losses = 0
        att_loss_pct = 0
        def_loss_pct = 0
        if att_action == "assault":
            att_loss_pct = 0.06 if (battle.wall_integrity or 100) <= 40 else 0.12
            if def_action == "ambush":
                att_loss_pct += 0.05
            def_loss_pct = 0.08 if (battle.wall_integrity or 100) <= 40 else 0.04
            att_losses = self._apply_phase_losses(attacker_army, att_loss_pct)
            def_losses = self._apply_phase_losses(defender_army, def_loss_pct)
        elif def_action == "sally":
            att_loss_pct = 0.04
            def_loss_pct = 0.03
            att_losses = self._apply_phase_losses(attacker_army, att_loss_pct)
            def_losses = self._apply_phase_losses(defender_army, def_loss_pct)
        elif att_action in ("bombard", "mine"):
            def_loss_pct = 0.01
            def_losses = self._apply_phase_losses(defender_army, def_loss_pct)

        winner = None
        phase_transition = False
        result_note = "Siege continues."
        if battle.defender_supply <= 0 or battle.defender_morale <= 0:
            battle.attacker_score = 5
            battle.defender_score = 0
            battle.phase = "COMPLETE"
            winner = "Attacker"
            narration = "Hunger and fear broke the defense. The garrison can no longer hold."
            result_note = "Attacker wins because defender supply or morale reached 0."
        elif battle.attacker_supply <= 0 or battle.attacker_morale <= 0:
            battle.attacker_score = 0
            battle.defender_score = 5
            battle.phase = "COMPLETE"
            winner = "Defender"
            narration = "The besieging host lost cohesion and the siege collapsed."
            result_note = "Defender wins because attacker supply or morale reached 0."
        elif battle.wall_integrity <= 0:
            battle.phase = "STREETS"
            battle.siege_phase = "STREETS"
            phase_transition = True
            battle.current_odds = int(self._clamp(battle.current_odds + 10, 10, 90))
            narration = "The walls gave way. The next turn will decide the fighting in the streets."
            result_note = f"Walls reached 0. Street odds base is now {battle.current_odds}."
        else:
            narration = (
                f"The attackers chose **{att_action}** while the defenders chose **{def_action}**."
                f"{blockade_text}"
            )

        gm_audit = (
            f"**Siege Walls Turn Audit**\n"
            f"Turn: `{battle.round_number or 0}`\n"
            f"Actions: attacker `{att_action}` / defender `{def_action}`\n\n"
            f"Attacker effect after interactions: `{att_effect}`\n"
            f"Defender effect after interactions: `{def_effect}`\n"
            f"Special notes: `{'; '.join(special_notes) if special_notes else 'none'}`\n\n"
            f"Wall delta: attacker `{att_effect.get('wall', 0):+}` + defender `{def_effect.get('wall', 0):+}` "
            f"+ random `{wall_random:+}` = `{wall_delta:+}`\n"
            f"Walls: `{old_wall}` -> `{battle.wall_integrity}`\n\n"
            f"Attacker supply: `{old_att_supply}` + `{att_supply_delta:+}` = `{battle.attacker_supply}`\n"
            f"Defender supply: `{old_def_supply}` + `{def_supply_delta:+}` = `{battle.defender_supply}`\n"
            f"Attacker morale: `{old_att_morale}` + `{att_morale_delta:+}` = `{battle.attacker_morale}`\n"
            f"Defender morale: `{old_def_morale}` + `{def_morale_delta:+}` = `{battle.defender_morale}`\n\n"
            f"Casualty rates: attacker `{att_loss_pct:.0%}` / defender `{def_loss_pct:.0%}`\n"
            f"Casualties: attacker `{att_losses}` / defender `{def_losses}`\n"
            f"Result check: {result_note}"
        )

        battle.attacker_plan = SIEGE_DEFAULT_ATTACKER_ACTION
        battle.defender_plan = SIEGE_DEFAULT_DEFENDER_ACTION

        await self.session.commit()
        turn_msg = (
            f"Siege Turn **{battle.round_number}**\n"
            f"Walls: **{battle.wall_integrity}** | "
            f"Supplies: Attacker **{battle.attacker_supply}**, Defender **{battle.defender_supply}** | "
            f"Morale: Attacker **{battle.attacker_morale}**, Defender **{battle.defender_morale}**"
        )
        return (
            battle,
            turn_msg,
            winner,
            phase_transition,
            narration,
            {"attacker": att_losses, "defender": def_losses, "_gm_audit": gm_audit},
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
        field_winner = None
        if battle.battle_type in ("LAND_BATTLE", "SEA_BATTLE"):
            field_winner = self._field_phase_winner(battle)

        if field_winner == "Attacker" or (
            field_winner is None and battle.attacker_score >= battle.defender_score
        ):
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
        winner_pre_rout = winner.troop_count
        loser_pre_rout = loser.troop_count
        winner_rout_losses = apply_rout_losses(winner, win_pct, False)
        loser_rout_losses = apply_rout_losses(loser, los_pct, True)
        winner_after_rout = winner.troop_count
        loser_after_rout = loser.troop_count

        commander_fate_str = ""
        is_destroyed = False
        is_siege_victory = battle.battle_type == "SIEGE" and winner == attacker_army
        retreat_odds = None
        retreat_roll = None
        fate_roll = None

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
                retreat_roll = random.randint(1, 100)

                if retreat_roll <= retreat_odds:
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

        aftermath_audit = (
            f"**Aftermath Casualty Audit**\n"
            f"Battle type: `{battle.battle_type}`\n"
            f"Winner: `{winner.commander_name}`\n"
            f"Loser: `{loser.commander_name}`\n"
            f"Loser score: `{loser_score}` -> score index `clamp(5 - {loser_score}, 0, 5) = {score_index}`\n"
            f"Winner rout pct: `{win_pct:.0%}` | Loser rout pct: `{los_pct:.0%}`\n\n"
            f"Winner rout losses: `int({winner_pre_rout} * {win_pct}) = {winner_rout_losses}`\n"
            f"Loser rout losses: `int({loser_pre_rout} * {los_pct}) = {loser_rout_losses}`\n"
            f"Winner survivors after rout: `{winner_after_rout}`\n"
            f"Loser survivors after rout before fate: `{loser_after_rout}`\n\n"
            f"Retreat odds: `{retreat_odds if retreat_odds is not None else 'n/a'}`\n"
            f"Retreat roll: `{retreat_roll if retreat_roll is not None else 'n/a'}`\n"
            f"Fate roll on failed retreat: `{fate_roll if fate_roll is not None else 'n/a'}`\n"
            f"Destroyed: `{is_destroyed}`\n"
            f"Loot transferred: `{loot}`"
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
            "_gm_audit": aftermath_audit,
        }

        return final_report, guild_id, notif_data

    async def cancel_battle_without_aftermath(self, battle_id: int):
        """
        GM cancellation path for battles that should end without applying rout,
        loot, commander fate, or ownership consequences.
        """
        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(
                selectinload(Battle.game),
                selectinload(Battle.fief),
                selectinload(Battle.attacker).selectinload(Army.house),
                selectinload(Battle.defender).selectinload(Army.house),
            )
        )
        battle = (await self.session.execute(stmt)).scalars().first()
        if not battle:
            return "Error: Battle not found.", None, {}

        guild_id = battle.game.guild_id if battle.game else None
        attacker = battle.attacker
        defender = battle.defender
        att_name = attacker.commander_name if attacker else "Unknown"
        def_name = defender.commander_name if defender else "Unknown"
        att_count = attacker.troop_count if attacker else 0
        def_count = defender.troop_count if defender else 0
        battle_type = battle.battle_type
        round_number = battle.round_number or 0
        score = f"{battle.attacker_score or 0}-{battle.defender_score or 0}"

        await self.session.delete(battle)
        await self.session.commit()

        report = (
            f"**Battle Cancelled by GM**\n\n"
            f"No aftermath was applied. No rout casualties, loot transfer, commander fate, "
            f"or siege ownership changes were processed.\n\n"
            f"**Attacker ({att_name})**\nSurvivors: {att_count}\n\n"
            f"**Defender ({def_name})**\nSurvivors: {def_count}"
        )
        audit = (
            f"**Forced End Cancellation Audit**\n"
            f"Battle type: `{battle_type}`\n"
            f"Rounds/turns resolved: `{round_number}`\n"
            f"Score at cancellation: `{score}`\n"
            f"Casualties applied by forced end: `0`\n"
            f"Reason: battle was cancelled instead of resolved through aftermath."
        )
        return report, guild_id, {"_gm_audit": audit}

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
            phase="SKIRMISH",
            round_number=0,
            terrain="unknown",
            attacker_morale=100,
            defender_morale=100,
            attacker_plan="cautious",
            defender_plan="cautious",
            attacker_supply=100,
            defender_supply=100,
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
        Auto-Battle phase processing. Returns 4-element tuple for Celery.
        """
        battle, report, winner, phase_transition, _, _ = (
            await self.process_field_battle_phase(battle_id)
        )
        return battle, report, winner, phase_transition

    async def _legacy_process_auto_battle_round(self, battle_id: int):
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
        battle.round_number = (battle.round_number or 0) + 1

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
            phase="WALLS",
            round_number=0,
            terrain="fortification",
            attacker_morale=100,
            defender_morale=100,
            attacker_supply=100,
            defender_supply=100,
            attacker_plan=SIEGE_DEFAULT_ATTACKER_ACTION,
            defender_plan=SIEGE_DEFAULT_DEFENDER_ACTION,
            wall_integrity=100,
            blockade_fleet_id=None,
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

        att_value = att_bp + (att_mar / 3)
        def_value = def_bp + (def_mar / 3) + def_bonus
        final_odds = int(max(10, min(90, odds)))
        calc_log = (
            f"**Siege Initial Odds**\n"
            f"Attacker BP: `{att_bp:.2f}`\n"
            f"Attacker martial: `{att_mar}` / 3 = `{att_mar / 3:.2f}`\n"
            f"Attacker value: `{att_value:.2f}`\n\n"
            f"Defender BP: `{def_bp:.2f}`\n"
            f"Defender martial: `{def_mar}` / 3 = `{def_mar / 3:.2f}`\n"
            f"Defense bonus `{defense_bonus_str}`: `{def_bonus}`\n"
            f"Defender value: `{def_value:.2f}`\n\n"
            f"Raw odds: `50 + ({att_value:.2f} - {def_value:.2f}) = {odds:.2f}`\n"
            f"Final current odds: `clamp({odds:.2f}, 10, 90) = {final_odds}`\n"
            f"Starting state: walls `100`, attacker supply `100`, defender supply `100`, "
            f"attacker morale `100`, defender morale `100`"
        )
        return reloaded, f"Odds: {final_odds}", calc_log

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
