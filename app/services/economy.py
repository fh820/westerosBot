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
        self,
        source_house_id: int,
        target_category: str,
        target_id: int,
        amount: int,
        source_fief_id: int = None,
    ):
        """
        Executes a gold transfer.
        - Deducts from a specific Fief (or the Source House's capital).
        - Adds to an Army, specific Fief, or Target House's capital.
        """
        # 1. Determine Source of Funds (Fief Treasury)
        source_fief = None

        if source_fief_id:
            source_fief = await self.session.get(Fief, source_fief_id)
        else:
            # Fallback: Find the Capital (First Fief) of the source house
            stmt = (
                select(Fief)
                .where(Fief.owner_id == source_house_id)
                .order_by(Fief.fief_id.asc())
                .limit(1)
            )
            source_fief = (await self.session.execute(stmt)).scalars().first()

        if not source_fief:
            return False, "❌ Source Fief not found or House has no lands."

        # Check Balance
        current_gold = source_fief.treasury or 0
        if current_gold < amount:
            return (
                False,
                f"❌ Insufficient funds in **{source_fief.name}**. Available: {current_gold}.",
            )

        # 2. Determine Target Destination
        target_name = "Unknown"
        target_found = False

        if target_category == "ARMY":
            target_obj = await self.session.get(Army, target_id)
            if target_obj:
                target_obj.treasury = (target_obj.treasury or 0) + amount
                target_name = target_obj.commander_name
                target_found = True

        elif target_category == "FIEF":
            target_obj = await self.session.get(Fief, target_id)
            if target_obj:
                target_obj.treasury = (target_obj.treasury or 0) + amount
                target_name = target_obj.name
                target_found = True

        elif target_category == "HOUSE":
            # Deposit into Target House's Capital
            stmt = (
                select(Fief)
                .where(Fief.owner_id == target_id)
                .order_by(Fief.fief_id.asc())
                .limit(1)
            )
            target_fief = (await self.session.execute(stmt)).scalars().first()
            if target_fief:
                target_fief.treasury = (target_fief.treasury or 0) + amount
                target_name = f"{target_fief.name} (Capital)"
                target_found = True
            else:
                return False, "❌ Target House has no lands to receive gold."

        if not target_found:
            return False, "❌ Target destination not found."

        # 3. Execute Deduction
        source_fief.treasury -= amount

        await self.session.commit()
        return (
            True,
            f"✅ Transferred **{amount} Gold** from **{source_fief.name}** to **{target_name}**.",
        )

    async def run_fiscal_year(self, game_id: int) -> list[str]:
        """
        Calculates fiscal year.
        1. Fiefs generate income locally.
        2. Taxes are paid to Liege.
           - Logic: House Treasury -> Fief Treasuries -> Army Treasuries.
           - If total liquid cash < tax, payment fails.
        """
        # 1. Load Data
        game = await self.session.get(Game, game_id)
        if not game:
            return ["❌ Game not found."]

        income_mods = game.income_modifiers or {}
        mod_global = income_mods.get("global", 1.0)
        mod_regions = income_mods.get("regions", {})
        mod_houses = income_mods.get("houses", {})

        # UPDATE: We must load Armies now to check their gold for taxes
        stmt_houses = (
            select(House)
            .where(House.game_id == game_id)
            .options(
                selectinload(House.fiefs), selectinload(House.armies)  # <--- ADDED THIS
            )
        )
        houses = (await self.session.execute(stmt_houses)).scalars().all()
        house_map = {h.house_id: h for h in houses}

        # =========================================================
        # 🕵️ PRE-FLIGHT CYCLE DETECTION
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
            return [f"❌ **CRITICAL DATA ERROR: FEUDAL LOOPS**\n{detected_cycles[0]}"]
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
        modifier_reports = {}

        # 2. STEP ONE: Fief Income Generation (Local)
        for house in houses:
            # Manpower (Global to House)
            active_fiefs = [f for f in house.fiefs if not f.is_ruined]
            if active_fiefs:
                m_cap = sum(f.base_manpower for f in active_fiefs)
                region = active_fiefs[0].region or "The Crownlands"
                rate = getattr(self, "REPOPULATION_RATES", {}).get(region, 0.02)
                house.manpower_cap = m_cap
                house.manpower = min(m_cap, house.manpower + int(m_cap * rate))

            # Gold (Local to Fief)
            house_total_gross_income = 0

            for f in house.fiefs:
                if not f.is_ruined:
                    # Apply Modifiers
                    modifier = mod_global
                    if f.region in mod_regions:
                        modifier = mod_regions[f.region]
                    if str(house.house_id) in mod_houses:
                        modifier = mod_houses[str(house.house_id)]

                    # Calculate
                    modified_income = int(f.base_income * f.integration * modifier)

                    # Deposit directly into FIEF Treasury
                    f.treasury = (f.treasury or 0) + modified_income

                    house_total_gross_income += modified_income

                    # Integration recovery
                    if f.integration < 1.0:
                        f.integration = min(1.0, f.integration + 0.20)

                    if modifier != 1.0 and f.region not in modifier_reports:
                        if f.region not in modifier_reports:
                            modifier_reports[f.region] = []
                        modifier_reports[f.region].append(
                            f"  - *{house.name} income {modifier*100:.0f}%*"
                        )

            # Store total revenue to calculate tax obligation
            yearly_revenue[house.house_id] = house_total_gross_income

        # 3. STEP TWO: Tax Flow (Scavenge Logic)
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
                # --- SCAVENGE LOGIC START ---
                # Check if we have enough money globally before deducting anything

                liquid_house = house.treasury or 0
                liquid_fiefs = sum(f.treasury or 0 for f in house.fiefs)
                liquid_armies = sum(a.treasury or 0 for a in house.armies)

                total_liquid = liquid_house + liquid_fiefs + liquid_armies

                if total_liquid >= tax_amount:
                    remaining_to_pay = tax_amount

                    # 1. Drain House Treasury
                    take = min(liquid_house, remaining_to_pay)
                    house.treasury -= take
                    remaining_to_pay -= take

                    # 2. Drain Fiefs (Richest First)
                    if remaining_to_pay > 0:
                        rich_fiefs = sorted(
                            house.fiefs, key=lambda x: x.treasury or 0, reverse=True
                        )
                        for f in rich_fiefs:
                            if remaining_to_pay <= 0:
                                break
                            available = f.treasury or 0
                            take = min(available, remaining_to_pay)
                            f.treasury -= take
                            remaining_to_pay -= take

                    # 3. Drain Armies (Richest First)
                    if remaining_to_pay > 0:
                        rich_armies = sorted(
                            house.armies, key=lambda x: x.treasury or 0, reverse=True
                        )
                        for a in rich_armies:
                            if remaining_to_pay <= 0:
                                break
                            available = a.treasury or 0
                            take = min(available, remaining_to_pay)
                            a.treasury -= take
                            remaining_to_pay -= take

                    # Pay the Liege (Direct to House Treasury)
                    liege.treasury += tax_amount
                    yearly_revenue[
                        liege.house_id
                    ] += tax_amount  # Adds to liege's gross for next loop calculation

                    regional_reports[region_name].append(
                        f"  💸 {get_report_name(house)} ➔ {get_report_name(liege)}: `{tax_amount}g`"
                    )
                else:
                    # Failed to pay
                    shortfall = tax_amount - total_liquid
                    regional_reports[region_name].append(
                        f"  ⚠️ {get_report_name(house)}: **In Arrears** (Short by {shortfall}g)"
                    )
                # --- SCAVENGE LOGIC END ---

        await self.session.commit()

        # 4. Build Report
        header = "## 📜 Royal Fiscal Report\n*Income distributed to Fiefs. Taxes collected from all sources.*\n━━━━━━━━━━━━━━━━━━\n"
        if not regional_reports and not modifier_reports:
            return [header + "\n*No economic activity to report this year.*"]

        content_lines = []
        all_regions = sorted(
            set(regional_reports.keys()) | set(modifier_reports.keys())
        )

        for region in all_regions:
            content_lines.append(f"### 🚩 {region.upper()}")
            if region in regional_reports:
                content_lines.extend(regional_reports[region])
            if region in modifier_reports:
                content_lines.extend(list(set(modifier_reports[region])))
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
