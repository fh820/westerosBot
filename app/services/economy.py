# # from sqlalchemy import select, update
# # from sqlalchemy.orm import selectinload
# # from app.db.models import House, Fief, Game


# # class EconomyService:
# #     def __init__(self, session):
# #         self.session = session

# #     async def run_fiscal_year(self, game_id: int):
# #         """
# #         1. Calculates income for every house from Fiefs.
# #         2. Pays taxes to Liege Lords.
# #         3. Updates DB.
# #         4. Returns a report.
# #         """
# #         # 1. Fetch all Houses with their Fiefs
# #         stmt = (
# #             select(House)
# #             .where(House.game_id == game_id)
# #             .options(selectinload(House.fiefs))
# #         )
# #         houses = (await self.session.execute(stmt)).scalars().all()

# #         # Map {house_id: HouseObj} for easy lookup
# #         house_map = {h.house_id: h for h in houses}

# #         # Track stats
# #         report = []

# #         # 2. Calculate Base Income (Generated from Land)
# #         gross_incomes = {}  # house_id -> int

# #         for house in houses:
# #             income = sum(f.base_income for f in house.fiefs)
# #             gross_incomes[house.house_id] = income
# #             # Add base income to treasury immediately
# #             house.treasury += income

# #         # 3. Process Taxes (The Feudal Chain)
# #         # We do this in a separate pass so everyone pays based on their *Gross* income,
# #         # not including what they just received from vassals (simplifies logic).

# #         tax_rate = 0.10  # 10%

# #         for house in houses:
# #             if not house.liege_id or not house.paying_taxes:
# #                 continue

# #             liege = house_map.get(house.liege_id)
# #             if not liege:
# #                 continue  # Liege might be deleted/null

# #             # Calculate Tax
# #             income = gross_incomes.get(house.house_id, 0)
# #             tax_amount = int(income * tax_rate)

# #             if tax_amount > 0:
# #                 if house.treasury >= tax_amount:
# #                     house.treasury -= tax_amount
# #                     liege.treasury += tax_amount
# #                     # Log significant transfers
# #                     if tax_amount > 500:
# #                         report.append(
# #                             f"💸 **{house.name}** paid **{tax_amount}** to **{liege.name}**."
# #                         )
# #                 else:
# #                     report.append(
# #                         f"⚠️ **{house.name}** could not pay tax to **{liege.name}** (Debt)."
# #                     )

# #         # 4. Save
# #         await self.session.commit()

# #         return (
# #             "\n".join(report) if report else "Fiscal year complete. No major transfers."
# #         )


# from sqlalchemy import select, update, func
# from sqlalchemy.orm import selectinload
# from app.db.models import House, Fief


# class EconomyService:
#     def __init__(self, session):
#         self.session = session

#     # async def run_fiscal_year(self, game_id: int):
#     #     """
#     #     Orchestrates the end-of-year economic phase and returns chunked report pages.
#     #     """
#     #     print(f"--- 💰 Running Fiscal Year for Game ID: {game_id} ---")

#     #     # --- Step 1: Base Income ---
#     #     income_subquery = (
#     #         select(func.sum(Fief.base_income))
#     #         .where(Fief.owner_id == House.house_id)
#     #         .correlate(House)
#     #         .scalar_subquery()
#     #     )
#     #     await self.session.execute(
#     #         update(House)
#     #         .where(House.game_id == game_id)
#     #         .values(treasury=House.treasury + func.coalesce(income_subquery, 0))
#     #     )
#     #     print("  -> Phase 1: Base income added.")

#     #     # --- Step 2: Taxes ---
#     #     stmt = (
#     #         select(House)
#     #         .where(House.game_id == game_id)
#     #         .options(selectinload(House.liege), selectinload(House.fiefs))
#     #     )
#     #     all_houses = (await self.session.execute(stmt)).scalars().all()

#     #     house_map = {h.house_id: h for h in all_houses}

#     #     # --- CHUNKING LOGIC ---
#     #     # We will collect all transaction lines into one big list first
#     #     transaction_lines = []

#     #     for vassal in sorted(
#     #         all_houses, key=lambda h: h.name
#     #     ):  # Sort for consistent order
#     #         if not vassal.liege_id:
#     #             continue

#     #         liege = house_map.get(vassal.liege_id)
#     #         if not liege:
#     #             continue

#     #         # Tax Calculation
#     #         if not vassal.paying_taxes:
#     #             transaction_lines.append(
#     #                 f"叛 **{vassal.name}** refused taxes to **{liege.name}**."
#     #             )
#     #             continue

#     #         vassal_income = sum(f.base_income for f in vassal.fiefs)
#     #         tax_amount = int(vassal_income * 0.10)

#     #         if tax_amount <= 0:
#     #             continue

#     #         if vassal.treasury >= tax_amount:
#     #             vassal.treasury -= tax_amount
#     #             liege.treasury += tax_amount
#     #             transaction_lines.append(
#     #                 f"💸 **{vassal.name}** paid **{tax_amount}** to **{liege.name}**."
#     #             )
#     #         else:
#     #             transaction_lines.append(
#     #                 f" defaulting **{vassal.name}** could not afford taxes to **{liege.name}**."
#     #             )

#     #     print("  -> Phase 2: Taxes processed.")

#     #     # --- Final Report Assembly ---
#     #     # Now, we split the big list into chunks of 20 lines each
#     #     final_pages = []
#     #     final_pages.append(
#     #         "**__Fiscal Report__**\n✅ All houses have received their annual income from fiefs."
#     #     )

#     #     # Define chunk size (e.g., 20 lines per message)
#     #     chunk_size = 20

#     #     for i in range(0, len(transaction_lines), chunk_size):
#     #         chunk = transaction_lines[i : i + chunk_size]
#     #         page_content = "\n".join(chunk)
#     #         final_pages.append(page_content)

#     #     return final_pages


#     async def run_fiscal_year(self, game_id: int):
#         stmt = select(House).where(House.game_id == game_id).options(selectinload(House.fiefs))
#         houses = (await self.session.execute(stmt)).scalars().all()
#         house_map = {h.house_id: h for h in houses}
#         report = []

#         # 1. Calculate Income (With Integration Math)
#         gross_incomes = {}

#         for house in houses:
#             house_income = 0
#             for f in house.fiefs:
#                 # Math: 1000 gold * 0.1 integration = 100 gold
#                 real_income = int(f.base_income * f.integration)
#                 house_income += real_income

#                 # INCREASE INTEGRATION (Recovery)
#                 if f.integration < 1.0:
#                     f.integration = min(1.0, f.integration + 0.25)

#             gross_incomes[house.house_id] = house_income
#             house.treasury += house_income

#         # 2. Process Taxes
#         tax_rate = 0.10
#         for house in houses:
#             if not house.liege_id or not house.paying_taxes: continue
#             liege = house_map.get(house.liege_id)
#             if not liege: continue

#             tax_amount = int(gross_incomes.get(house.house_id, 0) * tax_rate)

#             if tax_amount > 0:
#                 if house.treasury >= tax_amount:
#                     house.treasury -= tax_amount
#                     liege.treasury += tax_amount
#                     if tax_amount > 500:
#                         report.append(f"💸 **{house.name}** paid **{tax_amount}** to **{liege.name}**.")
#                 else:
#                     report.append(f"⚠️ **{house.name}** failed to pay tax (Debt).")

#         await self.session.commit()
#         return "\n".join(report) if report else "Fiscal year complete."


from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.models import House, Fief, Army, GamePlayer

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
DEFAULT_REGEN = 0.02  # Fallback


class EconomyService:
    def __init__(self, session):
        self.session = session

    async def get_army_gold(self, army_id: int):
        """
        Retrieves gold amount for an Army/Fleet.
        """
        stmt = (
            select(Army)
            .where(Army.army_id == army_id)
            .options(selectinload(Army.house))
        )
        army = (await self.session.execute(stmt)).scalars().first()

        if not army:
            return None, 0, "Not Found"

        # Ensure not None
        gold = army.treasury if army.treasury else 0
        name = army.commander_name if army.commander_name else f"Army {army.army_id}"
        return army, gold, name

    async def execute_transfer(
        self, source_house_id: int, target_category: str, target_id: int, amount: int
    ):
        """
        Executes the transfer of gold.
        target_category: 'ARMY' (ID) or 'HOUSE' (ID)
        """
        # 1. Get Source House
        source = await self.session.get(House, source_house_id)
        if not source:
            return False, "❌ Source House not found."

        if source.treasury < amount:
            return False, f"❌ Insufficient funds. Treasury: {source.treasury}"

        # 2. Handle Target
        target_obj = None
        target_name = "Unknown"

        if target_category == "ARMY":
            target_obj = await self.session.get(Army, target_id)
            target_name = target_obj.commander_name if target_obj else "Army"
            if target_obj:
                # Initialize if None
                if target_obj.treasury is None:
                    target_obj.treasury = 0
                target_obj.treasury += amount

        elif target_category == "HOUSE":
            target_obj = await self.session.get(House, target_id)
            target_name = target_obj.name if target_obj else "House"
            if target_obj:
                target_obj.treasury += amount

        if not target_obj:
            return False, "❌ Target not found in database."

        # 3. Deduct from Source
        source.treasury -= amount

        await self.session.commit()
        return (
            True,
            f"✅ Transferred **{amount} Gold** from **{source.name}** to **{target_name}**.",
        )

    async def run_fiscal_year(self, game_id: int) -> list[str]:
        # 1. Fetch Houses and Fiefs
        stmt_houses = (
            select(House)
            .where(House.game_id == game_id)
            .options(selectinload(House.fiefs))
        )
        houses = (await self.session.execute(stmt_houses)).scalars().all()
        house_map = {h.house_id: h for h in houses}

        # 2. Manual Player Lookup (to avoid model changes)
        stmt_players = (
            select(GamePlayer)
            .where(GamePlayer.game_id == game_id)
            .options(selectinload(GamePlayer.character))
        )
        all_players = (await self.session.execute(stmt_players)).scalars().all()

        player_lookup = {}
        for p in all_players:
            if p.claimed_house_id and (
                p.is_primary or p.claimed_house_id not in player_lookup
            ):
                player_lookup[p.claimed_house_id] = p

        def get_name(h):
            p = player_lookup.get(h.house_id)
            char_name = p.character.name if p and p.character else None
            h_name = h.name.replace("[", "").replace("]", "")
            return f"**{char_name}** ({h_name})" if char_name else f"**House {h_name}**"

        # --- DATA PROCESSING ---
        yearly_revenue = {h.house_id: 0 for h in houses}
        # Group transactions: { "Region Name": [lines...] }
        regional_reports = {}

        # 3. Process Fief Income & Manpower
        for house in houses:
            active_fiefs = [f for f in house.fiefs if not f.is_ruined]

            # Manpower
            if active_fiefs:
                m_cap = sum(f.base_manpower for f in active_fiefs)
                region = active_fiefs[0].region
                rate = REPOPULATION_RATES.get(region, 0.05)  # Default 5%
                house.manpower = min(m_cap, house.manpower + int(m_cap * rate))
                house.manpower_cap = m_cap

            # Income
            fief_income = 0
            for f in house.fiefs:
                if not f.is_ruined:
                    fief_income += int(f.base_income * f.integration)
                if f.integration < 1.0:
                    f.integration = min(1.0, f.integration + 0.25)

            house.treasury += fief_income
            yearly_revenue[house.house_id] = fief_income

        # 4. Process Tax Flow
        taxable_houses = [h for h in houses if h.liege_id and h.paying_taxes]
        # Feudal sort: Vassals pay Heirs, Heirs pay Kings
        taxable_houses.sort(
            key=lambda h: (
                house_map.get(h.liege_id).liege_id is not None
                if house_map.get(h.liege_id)
                else False
            )
        )

        for house in taxable_houses:
            liege = house_map.get(house.liege_id)
            if not liege:
                continue

            rate = max(
                0.0, min(1.0, house.tax_rate if house.tax_rate is not None else 0.10)
            )
            amount = int(yearly_revenue[house.house_id] * rate)

            # Determine Region
            region_name = house.fiefs[0].region if house.fiefs else "The Realm"
            if region_name not in regional_reports:
                regional_reports[region_name] = []

            if amount > 0:
                if house.treasury >= amount:
                    house.treasury -= amount
                    liege.treasury += amount
                    yearly_revenue[liege.house_id] += amount

                    regional_reports[region_name].append(
                        f"  💸 {get_name(house)} ➔ {get_name(liege)}: `{amount}g`"
                    )
                else:
                    regional_reports[region_name].append(
                        f"  ⚠️ {get_name(house)}: **In Arrears** (Debt to {get_name(liege)})"
                    )

        await self.session.commit()

        # 5. Build Formatted Report Pages
        header = (
            "## 📜 Royal Fiscal Report\n"
            "*Taxes collected and integration restored across the Seven Kingdoms.*\n"
            "━━━━━━━━━━━━━━━━━━\n"
        )

        if not regional_reports:
            return [header + "\n*Peace reigns; no taxes were exchanged this year.*"]

        # Sort regions alphabetically for consistency
        sorted_regions = sorted(regional_reports.keys())

        content_lines = []
        for region in sorted_regions:
            lines = regional_reports[region]
            if not lines:
                continue

            content_lines.append(f"### 🚩 {region.upper()}")
            content_lines.extend(lines)
            content_lines.append("")  # Spacer

        # 6. Pagination (Discord 2000 char limit)
        pages = []
        current_page = header

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
        Predicts how much tax a Lord will get.
        Shows actual names instead of IDs.
        """
        stmt = (
            select(House)
            .where(House.liege_id == liege_house_id)
            .options(selectinload(House.fiefs))
        )
        vassals = (await self.session.execute(stmt)).scalars().all()

        total = 0
        reports = []
        for v in vassals:
            # Calculate what they make from land
            gross = sum(
                int(f.base_income * f.integration) for f in v.fiefs if not f.is_ruined
            )
            rate = max(0.0, min(1.0, v.tax_rate if v.tax_rate is not None else 0.10))
            tax = int(gross * rate)
            total += tax
            reports.append((v.name.replace("[", "").replace("]", ""), gross, rate, tax))

        return reports, total
