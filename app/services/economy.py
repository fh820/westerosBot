from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.db.models import House, Fief, Army, GamePlayer, User, Character, Game
from sqlalchemy.orm.attributes import flag_modified
import datetime

REPOPULATION_RATES = {
    "The North": 0.02,
    "Dorne": 0.03,
    "Iron Islands": 0.05,
    "The Reach": 0.01,
    "The Riverlands": 0.05,
    "The Westerlands": 0.02,
    "The Vale": 0.03,
    "The Stormlands": 0.03,
    "The Crownlands": 0.02,
}


class EconomyService:
    def __init__(self, session):
        self.session = session

    async def get_army_gold(self, army_id: int):
        stmt = (
            select(Army)
            .where(Army.army_id == army_id)
            .options(selectinload(Army.house))
        )
        army = (await self.session.execute(stmt)).scalars().first()
        if not army:
            return None, 0, "Not Found"
        gold = army.treasury or 0
        name = army.commander_name or f"Army {army.army_id}"
        return army, gold, name

    async def execute_transfer(
        self, source_house_id: int, target_category: str, target_id: int, amount: int
    ):
        source = await self.session.get(House, source_house_id)
        if not source or source.treasury < amount:
            return False, "❌ Source House not found or insufficient funds."

        target_obj = None
        target_name = "Unknown"

        if target_category == "ARMY":
            target_obj = await self.session.get(Army, target_id)
            if target_obj:
                target_obj.treasury = (target_obj.treasury or 0) + amount
                target_name = target_obj.commander_name
        elif target_category == "HOUSE":
            target_obj = await self.session.get(House, target_id)
            if target_obj:
                target_obj.treasury += amount
                target_name = target_obj.name

        if not target_obj:
            return False, "❌ Target not found."

        source.treasury -= amount
        await self.session.commit()
        return (
            True,
            f"✅ Transferred **{amount} Gold** from **{source.name}** to **{target_name}**.",
        )

    async def run_fiscal_year(self, game_id: int) -> list[str]:
        """
        Calculates fiscal year, applying income modifiers and checking for feudal loops.
        """
        # 1. Load Data (Game, Houses, Players)
        game = await self.session.get(Game, game_id)
        if not game:
            return ["❌ Game not found."]

        income_mods = game.income_modifiers or {}
        mod_global = income_mods.get("global", 1.0)
        mod_regions = income_mods.get("regions", {})
        mod_houses = income_mods.get("houses", {})

        stmt_houses = (
            select(House)
            .where(House.game_id == game_id)
            .options(selectinload(House.fiefs))
        )
        houses = (await self.session.execute(stmt_houses)).scalars().all()
        house_map = {h.house_id: h for h in houses}

        # =========================================================
        # 🕵️ PRE-FLIGHT CYCLE DETECTION (Kept from Debug Version)
        # =========================================================
        detected_cycles = []
        for h in houses:
            current_chain_ids, path_names = set(), []
            curr = h
            while curr.liege_id and curr.liege_id in house_map:
                if curr.house_id in current_chain_ids:
                    path_names.append(f"**{curr.name}** (ID {curr.house_id}) 🔄")
                    detected_cycles.append(" -> ".join(path_names))
                    break
                current_chain_ids.add(curr.house_id)
                path_names.append(f"{curr.name} ({curr.house_id})")
                curr = house_map[curr.liege_id]
                if curr.house_id in current_chain_ids:
                    path_names.append(f"**{curr.name}** (ID {curr.house_id}) 🔄")
                    detected_cycles.append(" -> ".join(path_names))
                    break
        if detected_cycles:
            unique_cycles = list(set(detected_cycles))
            error_msg = "❌ **CRITICAL DATA ERROR: FEUDAL LOOPS DETECTED**\n..."
            for cycle in unique_cycles[:10]:
                error_msg += f"🔸 {cycle}\n"
            error_msg += (
                "\n**Solution:** Use SQL to fix the `liege_id` of one of these houses."
            )
            return [error_msg]
        # =========================================================

        stmt_players = (
            select(GamePlayer)
            .where(GamePlayer.game_id == game_id)
            .options(selectinload(GamePlayer.character))
        )
        player_lookup = {
            p.claimed_house_id: p
            for p in (await self.session.execute(stmt_players)).scalars().all()
            if p.claimed_house_id
        }

        def get_report_name(h):
            p = player_lookup.get(h.house_id)
            char_name = p.character.name if p and p.character else None
            clean_house_name = h.name.replace("[", "").replace("]", "")
            return (
                f"**{char_name}** ({clean_house_name})"
                if char_name
                else f"**House {clean_house_name}**"
            )

        yearly_revenue = {h.house_id: 0 for h in houses}
        regional_reports = {}
        modifier_reports = {}  # To track which modifiers were applied

        # 2. STEP ONE: Fief Income & Manpower Regen
        for house in houses:
            # Manpower calculation (unchanged)
            active_fiefs = [f for f in house.fiefs if not f.is_ruined]
            if active_fiefs:
                m_cap = sum(f.base_manpower for f in active_fiefs)
                region = active_fiefs[0].region or "The Crownlands"
                # You might need to define REPOPULATION_RATES somewhere
                rate = getattr(self, "REPOPULATION_RATES", {}).get(region, 0.02)
                house.manpower_cap = m_cap
                house.manpower = min(m_cap, house.manpower + int(m_cap * rate))

            # Base Income calculation (WITH MODIFIERS)
            fief_income = 0
            for f in house.fiefs:
                if not f.is_ruined:
                    # --- NEW LOGIC: APPLY MODIFIER ---
                    # Precedence: House > Region > Global
                    modifier = mod_global
                    if f.region in mod_regions:
                        modifier = mod_regions[f.region]
                    if str(house.house_id) in mod_houses:  # JSON keys must be strings
                        modifier = mod_houses[str(house.house_id)]

                    # Apply the modifier and add to income
                    modified_income = int(f.base_income * f.integration * modifier)
                    fief_income += modified_income

                    # Track for report if a modifier was used
                    if modifier != 1.0:
                        if f.region not in modifier_reports:
                            modifier_reports[f.region] = []
                        modifier_reports[f.region].append(
                            f"  - *{house.name} income modified by {modifier*100:.0f}%*"
                        )
                    # --- END NEW LOGIC ---

                    if f.integration < 1.0:
                        f.integration = min(1.0, f.integration + 0.20)

            house.treasury += fief_income
            yearly_revenue[house.house_id] = fief_income

        # 3. STEP TWO: Tax Flow (Bottom-Up Logic)
        def get_feudal_depth(h, depth=0):
            if not h.liege_id or h.liege_id not in house_map:
                return depth
            return get_feudal_depth(house_map[h.liege_id], depth + 1)

        taxable_houses = [h for h in houses if h.liege_id and h.paying_taxes]
        taxable_houses.sort(key=lambda h: get_feudal_depth(h), reverse=True)

        for house in taxable_houses:
            liege = house_map.get(house.liege_id)
            if not liege:
                continue

            rate = max(
                0.0, min(1.0, house.tax_rate if house.tax_rate is not None else 0.10)
            )
            tax_amount = int(yearly_revenue[house.house_id] * rate)

            region_name = house.fiefs[0].region if house.fiefs else "The Realm"
            if region_name not in regional_reports:
                regional_reports[region_name] = []

            if tax_amount > 0:
                if house.treasury >= tax_amount:
                    house.treasury -= tax_amount
                    liege.treasury += tax_amount
                    yearly_revenue[
                        liege.house_id
                    ] += tax_amount  # Liege's income for THEIR taxes
                    regional_reports[region_name].append(
                        f"  💸 {get_report_name(house)} ➔ {get_report_name(liege)}: `{tax_amount}g`"
                    )
                else:
                    regional_reports[region_name].append(
                        f"  ⚠️ {get_report_name(house)}: **In Arrears** (Cannot pay {get_report_name(liege)})"
                    )

        await self.session.commit()

        # 4. Build Report
        header = "## 📜 Royal Fiscal Report\n*Taxes collected and integration restored.*\n━━━━━━━━━━━━━━━━━━\n"
        if not regional_reports and not modifier_reports:
            return [header + "\n*No economic activity to report this year.*"]

        content_lines = []
        # Combine tax and modifier reports for a clean output
        all_regions = sorted(
            set(regional_reports.keys()) | set(modifier_reports.keys())
        )

        for region in all_regions:
            content_lines.append(f"### 🚩 {region.upper()}")
            # Add tax lines first
            if region in regional_reports:
                content_lines.extend(regional_reports[region])
            # Add modifier notes after
            if region in modifier_reports:
                content_lines.extend(
                    list(set(modifier_reports[region]))
                )  # Use set to avoid duplicate notes
            content_lines.append("")

        # 5. Pagination
        pages, current_page = [], header
        for line in content_lines:
            if len(current_page) + len(line) > 1900:
                pages.append(current_page)
                current_page = ""
            current_page += line + "\n"
        pages.append(current_page)
        return pages

    async def calculate_tax_income_for_house(
        self, liege_house_id: int
    ) -> tuple[list, int]:
        """
        Calculates the projected tax income a liege will receive from their direct vassals.
        This now correctly applies any active global, regional, or house-specific income modifiers.
        """
        # 1. Load Game Data to get Income Modifiers
        # We need the game_id, which we can get from the liege house itself.
        liege_house = await self.session.get(House, liege_house_id)
        if not liege_house:
            return [], 0  # Return empty if the liege house doesn't exist

        game = await self.session.get(Game, liege_house.game_id)
        if not game:
            return [], 0

        # Load modifiers with safe defaults
        income_mods = game.income_modifiers or {}
        mod_global = income_mods.get("global", 1.0)
        mod_regions = income_mods.get("regions", {})
        mod_houses = income_mods.get("houses", {})

        # 2. Find all direct vassals of the liege
        stmt = (
            select(House)
            .where(House.liege_id == liege_house_id)
            .options(selectinload(House.fiefs))  # Eagerly load fiefs
        )
        vassals = (await self.session.execute(stmt)).scalars().all()

        total_tax_income = 0
        vassal_reports = []

        # 3. Calculate income for each vassal
        for vassal in vassals:
            gross_modified_income = 0
            for fief in vassal.fiefs:
                if not fief.is_ruined:
                    # --- APPLY MODIFIER LOGIC (Same as run_fiscal_year) ---
                    # Precedence: House > Region > Global
                    modifier = mod_global
                    if fief.region in mod_regions:
                        modifier = mod_regions[fief.region]
                    # JSON keys must be strings, so we convert the house ID
                    if str(vassal.house_id) in mod_houses:
                        modifier = mod_houses[str(vassal.house_id)]

                    # Apply the final modifier to this fief's income
                    modified_income = int(
                        fief.base_income * fief.integration * modifier
                    )
                    gross_modified_income += modified_income

            # 4. Calculate Tax based on the MODIFIED income
            tax_rate = vassal.tax_rate if vassal.tax_rate is not None else 0.10
            tax_due = int(gross_modified_income * tax_rate)
            total_tax_income += tax_due

            vassal_reports.append(
                (
                    vassal.name.replace("[", "").replace("]", ""),
                    gross_modified_income,
                    tax_rate,
                    tax_due,
                )
            )

        return vassal_reports, total_tax_income

    # In your EconomyService class
    async def set_income_modifier(
        self, game_id: int, mod_type: str, target: str, value_str: str
    ):
        """Sets or resets an income modifier for the entire game."""
        game = await self.session.get(Game, game_id)
        if not game:
            return False, "Game not found."

        # 1. Parse the value
        value = None
        if value_str == "reset":
            value = None  # Sentinel for deletion
        elif value_str == "half":
            value = 0.5
        elif value_str == "full":
            value = 1.0
        elif value_str.endswith("%"):
            try:
                value = float(value_str.strip("%")) / 100.0
            except ValueError:
                return False, "Invalid percentage."
        else:
            return (
                False,
                "Invalid value. Use 'half', 'full', 'reset', or a percentage (e.g., '50%').",
            )

        # 2. Get the current modifiers
        modifiers = game.income_modifiers.copy() if game.income_modifiers else {}

        # 3. Apply the change
        if mod_type == "global":
            if value is None:
                modifiers.pop("global", None)
            else:
                modifiers["global"] = value
            msg = f"Global income modifier set to {value_str}."

        elif mod_type == "region":
            if "regions" not in modifiers:
                modifiers["regions"] = {}
            if value is None:
                modifiers["regions"].pop(target, None)
            else:
                modifiers["regions"][target] = value
            msg = f"Income modifier for region '{target}' set to {value_str}."

        elif mod_type == "house":
            # Find house by name to get its ID
            stmt = select(House.house_id).where(
                House.game_id == game_id, House.name.ilike(target)
            )
            house_id = (await self.session.execute(stmt)).scalar()
            if not house_id:
                return False, f"House '{target}' not found."

            if "houses" not in modifiers:
                modifiers["houses"] = {}
            if value is None:
                modifiers["houses"].pop(str(house_id), None)
            else:
                modifiers["houses"][str(house_id)] = value  # JSON keys must be strings
            msg = f"Income modifier for House {target} set to {value_str}."

        else:
            return False, "Invalid modifier type. Use 'global', 'region', or 'house'."

        # 4. Save to DB
        game.income_modifiers = modifiers
        flag_modified(game, "income_modifiers")
        await self.session.commit()
        return True, msg
