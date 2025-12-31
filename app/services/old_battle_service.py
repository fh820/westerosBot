import random
import copy
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.repositories import ArmyRepo
from app.db.models import Army, Character, GamePlayer, House, Battle, Fief

# --- CONFIG ---
UNIT_STATS = {
    "knights": {"value": 15.0, "price": 10},
    "cavalry": {"value": 5.0, "price": 4},
    "infantry": {"value": 3.5, "price": 3},
    "archers": {"value": 2.5, "price": 2},
    "militia": {"value": 1.0, "price": 1},
    "warships": {"value": 100.0, "price": 200},
}
# Casualty Tables (Keys 0-10 represent score difference/rounds lost)
WINNER_CASUALTY_TABLE = {
    0: 0.05,
    1: 0.10,
    2: 0.15,
    3: 0.20,
    4: 0.25,
    5: 0.30,
    6: 0.35,
    7: 0.40,
    8: 0.45,
    9: 0.50,
}
LOSER_CASUALTY_TABLE = {
    0: 0.0,
    1: 0.0,
    2: 0.0,
    3: 0.0,
    4: 0.0,
    5: 0.45,
    6: 0.50,
    7: 0.60,
    8: 0.70,
    9: 0.80,
    10: 0.90,
}


class BattleService:
    def __init__(self, session):
        self.session = session

    async def _get_character_martial(self, house_id: int, commander_name: str) -> int:
        if not commander_name:
            return 0
        stmt = select(Character).where(
            Character.house_id == house_id, Character.name.ilike(commander_name)
        )
        char = (await self.session.execute(stmt)).scalars().first()
        return char.skills.get("martial", 0) if char and char.skills else 0

    def _calculate_army_bp(self, army):
        total_value = sum(
            count * UNIT_STATS.get(unit.lower(), {}).get("value", 0)
            for unit, count in army.composition.items()
        )
        # 🚨 FIX: Return Tuple (Raw, Scaled)
        return total_value, total_value / 250.0

    async def start_battle(
        self,
        game_id: int,
        attacker_id: int,
        defender_id: int,
        ambush: str,
        defense: str,
    ):
        stmt = (
            select(Army)
            .where(Army.army_id.in_([attacker_id, defender_id]))
            .options(selectinload(Army.house).selectinload(House.characters))
        )
        armies = (await self.session.execute(stmt)).scalars().all()
        attacker = next((a for a in armies if a.army_id == attacker_id), None)
        defender = next((a for a in armies if a.army_id == defender_id), None)
        if not attacker or not defender:
            return None, "❌ Armies not found.", None

        att_val, att_bp = self._calculate_army_bp(attacker)
        def_val, def_bp = self._calculate_army_bp(defender)

        att_martial = await self._get_character_martial(
            attacker.house_id, attacker.commander_name
        )
        def_martial = await self._get_character_martial(
            defender.house_id, defender.commander_name
        )

        ambush_bonuses = {"extreme": 15, "good": 10, "decent": 5, "failed": -5}
        def_bonuses = {"major": 20, "significant": 10, "minor": 5}

        att_bonus = att_martial + ambush_bonuses.get(ambush.lower(), 0)
        def_bonus = def_martial + def_bonuses.get(defense.lower(), 0)

        if attacker.troop_count > defender.troop_count * 1.2:
            att_bonus += 4
        elif defender.troop_count > attacker.troop_count * 1.2:
            def_bonus += 4

        odds = 50 + ((att_bp + att_bonus) - (def_bp + def_bonus))
        odds = int(max(1, min(99, odds)))

        new_battle = Battle(
            game_id=game_id,
            attacker_id=attacker.army_id,
            defender_id=defender.army_id,
            current_odds=odds,
            battle_type="FIELD",
        )
        self.session.add(new_battle)
        await self.session.commit()

        calc_log = (
            f"**Attacker:** Units `{att_bp:.1f}` + Mar `{att_martial}`\n"
            f"**Defender:** Units `{def_bp:.1f}` + Mar `{def_martial}`\n"
            f"**Formula:** 50 + ({att_bp+att_bonus:.1f}) - ({def_bp+def_bonus:.1f}) = **{odds}**"
        )
        return new_battle, f"Attacker Odds: 1 - {odds}", calc_log

    async def start_siege(
        self, game_id: int, attacker_id: int, fief_name: str, defense_bonus_str: str
    ):
        attacker = await ArmyRepo.get_army_by_id(self.session, attacker_id)
        stmt_att = (
            select(Army)
            .where(Army.army_id == attacker_id)
            .options(selectinload(Army.house).selectinload(House.characters))
        )
        attacker = (await self.session.execute(stmt_att)).scalars().first()

        stmt_f = select(Fief).where(Fief.game_id == game_id, Fief.name.ilike(fief_name))
        fief = (await self.session.execute(stmt_f)).scalars().first()
        if not fief:
            return None, "❌ Fief not found.", None

        stmt_d = (
            select(Army)
            .where(
                Army.house_id == fief.owner_id,
                Army.location_x == fief.location_x,
                Army.location_y == fief.location_y,
                Army.status == "GARRISONED",
            )
            .options(selectinload(Army.house).selectinload(House.characters))
        )
        defender = (await self.session.execute(stmt_d)).scalars().first()
        if not defender:
            return None, "❌ No garrison found.", None

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
        await self.session.commit()

        calc_log = (
            f"**Attacker:** Units `{att_bp:.1f}` + Mar `{att_martial}`\n"
            f"**Defender:** Units `{def_bp:.1f}` + Mar `{def_martial}` + Wall `{def_bonus}`\n"
            f"**Formula:** 50 + {att_total:.1f} - {def_total:.1f} = **{odds}**"
        )
        return new_battle, f"Attacker Odds (Walls): 1 - {odds}", calc_log

    # async def calculate_current_odds(
    #     self,
    #     battle_id: int,
    #     att_bonus: int,
    #     def_bonus: int,
    #     att_cmd_override: int = None,
    #     def_cmd_override: int = None,
    # ):
    #     battle = await self.session.get(
    #         Battle,
    #         battle_id,
    #         options=[
    #             selectinload(Battle.attacker)
    #             .selectinload(Army.house)
    #             .selectinload(House.characters),
    #             selectinload(Battle.defender)
    #             .selectinload(Army.house)
    #             .selectinload(House.characters),
    #         ],
    #     )
    #     if not battle:
    #         return None

    #     attacker, defender = battle.attacker, battle.defender
    #     _, att_bp = self._calculate_army_bp(attacker)
    #     _, def_bp = self._calculate_army_bp(defender)

    #     if att_cmd_override is not None:
    #         att_martial = att_cmd_override
    #     else:
    #         att_martial = await self._get_character_martial(
    #             attacker.house_id, attacker.commander_name
    #         )

    #     if def_cmd_override is not None:
    #         def_martial = def_cmd_override
    #     else:
    #         def_martial = await self._get_character_martial(
    #             defender.house_id, defender.commander_name
    #         )

    #     att_total = att_bp + att_martial + att_bonus
    #     def_total = def_bp + def_martial + def_bonus

    #     odds = 50 + (att_total - def_total)
    #     if attacker.troop_count > defender.troop_count * 1.1:
    #         odds += 2
    #     elif defender.troop_count > attacker.troop_count * 1.1:
    #         odds -= 2

    #     score_diff = battle.attacker_score - battle.defender_score
    #     odds += score_diff * 5

    #     battle.current_odds = int(max(1, min(99, odds)))
    #     await self.session.commit()
    #     return battle

    async def calculate_current_odds(
        self,
        battle_id: int,
        att_bonus: int,
        def_bonus: int,
        att_cmd_override: int = None,
        def_cmd_override: int = None,
    ):
        """
        Re-calculates odds based on current state and GM input.
        """
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
                selectinload(Battle.fief),  # <--- THIS WAS MISSING
            )
        )
        result = await self.session.execute(stmt)
        battle = result.scalars().first()

        if not battle:
            return None

        attacker, defender = battle.attacker, battle.defender

        # 1. Unit Power
        _, att_bp = self._calculate_army_bp(attacker)
        _, def_bp = self._calculate_army_bp(defender)

        # 2. Commander Power (Override > DB > 0)
        if att_cmd_override is not None:
            att_martial = att_cmd_override
        else:
            att_martial = await self._get_character_martial(
                attacker.house_id, attacker.commander_name
            )

        if def_cmd_override is not None:
            def_martial = def_cmd_override
        else:
            def_martial = await self._get_character_martial(
                defender.house_id, defender.commander_name
            )

        # 3. Totals
        att_total = att_bp + att_martial + att_bonus
        def_total = def_bp + def_martial + def_bonus

        # 4. Odds Calculation
        odds = 50 + (att_total - def_total)

        if attacker.troop_count > defender.troop_count * 1.1:
            odds += 2
        elif defender.troop_count > attacker.troop_count * 1.1:
            odds -= 2

        score_diff = battle.attacker_score - battle.defender_score
        odds += score_diff * 5

        battle.current_odds = int(max(1, min(99, odds)))
        await self.session.commit()
        return battle

    async def process_battle_round(self, battle_id: int):
        battle = await self.session.get(Battle, battle_id)
        if not battle:
            return None, "Battle not found.", None, None

        if battle.attacker_score >= 5 or battle.defender_score >= 5:
            if not (
                battle.battle_type == "SIEGE"
                and battle.siege_phase == "WALLS"
                and battle.attacker_score >= 5
            ):
                winner = (
                    "Attacker"
                    if battle.attacker_score > battle.defender_score
                    else "Defender"
                )
                return battle, "Battle already finished.", winner, False

        roll = random.randint(1, 100)
        is_attacker_win = roll <= battle.current_odds

        if is_attacker_win:
            battle.attacker_score += 1
        else:
            battle.defender_score += 1

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

        await self.session.commit()
        winner = None
        if (
            battle.battle_type == "SIEGE"
            and battle.siege_phase == "STREETS"
            and battle.attacker_score >= 5
        ) or (battle.battle_type == "FIELD" and battle.attacker_score >= 5):
            winner = "Attacker"
        elif battle.defender_score >= 5:
            winner = "Defender"
        return (
            battle,
            f"Roll: **{roll}**. Round to **{'Attacker' if is_attacker_win else 'Defender'}**",
            winner,
            phase_transition,
        )

    async def calculate_final_casualties(self, battle_id: int):
        battle = await self.session.get(
            Battle,
            battle_id,
            options=[selectinload(Battle.attacker), selectinload(Battle.defender)],
        )
        if not battle:
            return "Battle not found."

        attacker, defender = battle.attacker, battle.defender

        att_before_total = attacker.troop_count
        att_before_comp = dict(attacker.composition) if attacker.composition else {}
        def_before_total = defender.troop_count
        def_before_comp = dict(defender.composition) if defender.composition else {}

        if battle.attacker_score >= battle.defender_score:
            winner, loser = attacker, defender
            win_loss_idx = battle.defender_score
            lose_loss_idx = 5
        else:
            winner, loser = defender, attacker
            win_loss_idx = battle.attacker_score
            lose_loss_idx = 5

        win_loss_pct = WINNER_CASUALTY_TABLE.get(win_loss_idx, 0.50)
        lose_loss_pct = LOSER_CASUALTY_TABLE.get(lose_loss_idx, 0.90)

        def apply_losses(army, percent):
            new_count = int(army.troop_count * (1.0 - percent))
            new_comp, _ = ArmyRepo._calculate_split(
                army.composition, new_count, army.troop_count
            )
            army.troop_count = new_count
            army.composition = new_comp

        apply_losses(winner, win_loss_pct)
        apply_losses(loser, lose_loss_pct)

        loot = loser.treasury
        winner_house = await self.session.get(House, winner.house_id)
        if winner_house:
            winner_house.treasury += loot
        loser.treasury = 0

        capture_msg = ""
        if loser.troop_count <= 0:
            await self.session.delete(loser)

        # FIX: Only delete if not siege
        if battle.battle_type != "SIEGE":
            await self.session.delete(battle)

        await self.session.commit()

        def generate_breakdown(before, after):
            lines = []
            for unit, initial in before.items():
                if initial > 0:
                    survivors = after.composition.get(unit, 0)
                    lines.append(
                        f"- {unit.title()}: {survivors} survivors, {initial - survivors} lost"
                    )
            return "\n".join(lines) if lines else "- No details"

        att_bd = generate_breakdown(att_before_comp, attacker)
        def_bd = generate_breakdown(def_before_comp, defender)

        return (
            f"🏆 **Victor:** **{winner.commander_name}**\n\n"
            f"**Attacker ({attacker.commander_name})**\nSurvived: {attacker.troop_count}/{att_before_total}\n{att_bd}\n\n"
            f"**Defender ({defender.commander_name})**\nSurvived: {defender.troop_count}/{def_before_total}\n{def_bd}\n\n"
            f"💰 **Loot:** The victor seized **{loot}** Gold."
            f"{capture_msg}"
        )

    async def resolve_siege_consequences(self, battle_id: int):
        battle = await self.session.get(Battle, battle_id)
        if not battle or battle.battle_type != "SIEGE":
            return False, "Invalid siege."
        fief = await self.session.get(Fief, battle.fief_id)
        winner = await self.session.get(
            Army,
            (
                battle.attacker_id
                if battle.attacker_score >= battle.defender_score
                else battle.defender_id
            ),
        )
        if not fief or not winner:
            return False, "Data missing."

        if winner.army_id == battle.defender_id:
            await self.session.delete(battle)
            await self.session.commit()
            return True, f"🛡️ **Siege Repelled!** {fief.name} holds!"

        victim_house = await self.session.get(House, fief.owner_id)
        attacker_house = await self.session.get(House, winner.house_id)

        loot = victim_house.treasury if victim_house else 0
        if attacker_house:
            attacker_house.treasury += loot
        if victim_house:
            victim_house.treasury = 0

        fief.owner_id = winner.house_id
        fief.integration = 0.10
        winner.location_x, winner.location_y = fief.location_x, fief.location_y
        winner.status = "GARRISONED"
        winner.commander_name = f"Garrison of {fief.name}"

        await self.session.delete(battle)
        await self.session.commit()
        return True, (
            f"🏰 **{fief.name} has fallen!**\n"
            f"It is now held by **House {attacker_house.name if attacker_house else 'Unknown'}**.\n"
            f"💰 Treasury seized: **{loot}** Gold.\n"
            f"📉 Integration reset to **10%**."
        )
