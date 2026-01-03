from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.db.models import House, Fief, Army, GamePlayer, User, Character
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
        Debug Version: detect_cycles_before_running
        """
        # 1. Load Data
        stmt_houses = (
            select(House)
            .where(House.game_id == game_id)
            .options(selectinload(House.fiefs))
        )
        houses = (await self.session.execute(stmt_houses)).scalars().all()
        house_map = {h.house_id: h for h in houses}

        # =========================================================
        # 🕵️ DEBUG: PRE-FLIGHT CYCLE DETECTION
        # =========================================================
        detected_cycles = []

        # We check the chain for every house to ensure no infinite loops exist
        for h in houses:
            current_chain_ids = set()
            path_names = []

            curr = h
            # Traverse up the tree
            while curr.liege_id and curr.liege_id in house_map:
                # 1. Check if we have been here before in this specific chain
                if curr.house_id in current_chain_ids:
                    # CYCLE FOUND!
                    path_names.append(f"**{curr.name}** (ID {curr.house_id}) 🔄")
                    detected_cycles.append(" -> ".join(path_names))
                    break

                # 2. Add to history and move up
                current_chain_ids.add(curr.house_id)
                path_names.append(f"{curr.name} ({curr.house_id})")

                # Move to the liege
                curr = house_map[curr.liege_id]

                # 3. Check for immediate self-reference (House is its own Liege)
                if curr.house_id in current_chain_ids:
                    path_names.append(f"**{curr.name}** (ID {curr.house_id}) 🔄")
                    detected_cycles.append(" -> ".join(path_names))
                    break

        if detected_cycles:
            # Remove duplicates (since A->B is the same loop as B->A)
            unique_cycles = list(set(detected_cycles))
            error_msg = "❌ **CRITICAL DATA ERROR: FEUDAL LOOPS DETECTED**\nThe fiscal year cannot run because the following houses are stuck in infinite loops:\n\n"
            for cycle in unique_cycles[:10]:  # Limit to 10 to prevent spam
                error_msg += f"🔸 {cycle}\n"

            error_msg += "\n**Solution:** Use SQL to set one of these houses' `liege_id` to NULL or a valid King."
            return [error_msg]
        # =========================================================
        # END DEBUG SECTION
        # =========================================================

        # Identify players for the report naming
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

        # 2. STEP ONE: Fief Income & Manpower Regen
        for house in houses:
            active_fiefs = [f for f in house.fiefs if not f.is_ruined]

            # Manpower calculation
            if active_fiefs:
                m_cap = sum(f.base_manpower for f in active_fiefs)
                region = active_fiefs[0].region or "The Crownlands"
                rate = REPOPULATION_RATES.get(region, 0.02)
                house.manpower_cap = m_cap
                house.manpower = min(m_cap, house.manpower + int(m_cap * rate))

            # Base Income calculation
            fief_income = 0
            for f in house.fiefs:
                if not f.is_ruined:
                    fief_income += int(f.base_income * f.integration)
                    # Gradually restore integration over time
                    if f.integration < 1.0:
                        f.integration = min(1.0, f.integration + 0.20)

            house.treasury += fief_income
            yearly_revenue[house.house_id] = fief_income

        # 3. STEP TWO: Tax Flow (Bottom-Up Logic)

        # Standard recursive depth (safe now because we checked for cycles above)
        def get_feudal_depth(h, depth=0):
            if not h.liege_id or h.liege_id not in house_map:
                return depth
            return get_feudal_depth(house_map[h.liege_id], depth + 1)

        taxable_houses = [h for h in houses if h.liege_id and h.paying_taxes]
        # Sort by depth descending (Vassals [Depth 2] -> Heirs [Depth 1] -> King [Depth 0])
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
                    # Crucial: The liege now has more revenue to pay THEIR liege
                    yearly_revenue[liege.house_id] += tax_amount
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
        if not regional_reports:
            return [header + "\n*No taxes were exchanged this year.*"]

        content_lines = []
        for region in sorted(regional_reports.keys()):
            content_lines.append(f"### 🚩 {region.upper()}")
            content_lines.extend(regional_reports[region])
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
        stmt = (
            select(House)
            .where(House.liege_id == liege_house_id)
            .options(selectinload(House.fiefs))
        )
        vassals = (await self.session.execute(stmt)).scalars().all()
        total, reports = 0, []
        for v in vassals:
            gross = sum(
                int(f.base_income * f.integration) for f in v.fiefs if not f.is_ruined
            )
            rate = v.tax_rate if v.tax_rate is not None else 0.10
            tax = int(gross * rate)
            total += tax
            reports.append((v.name.replace("[", "").replace("]", ""), gross, rate, tax))
        return reports, total
