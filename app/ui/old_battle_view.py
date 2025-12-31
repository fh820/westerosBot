# import discord
# from sqlalchemy import select
# from sqlalchemy.orm import selectinload
# from app.db.db_manager import get_session
# from app.db.models import Battle, Army, House
# from app.services.battle_service import BattleService
# from app.ui.modals import ModifierModal


# class BattleControlView(discord.ui.View):
#     def __init__(self, battle_id: int):
#         super().__init__(timeout=None)  # Persistent View
#         self.battle_id = battle_id

#     async def _get_battle_fully_loaded(self, session):
#         stmt = (
#             select(Battle)
#             .where(Battle.id == self.battle_id)
#             .options(
#                 selectinload(Battle.attacker),
#                 selectinload(Battle.defender),
#                 selectinload(Battle.fief),
#             )
#         )
#         result = await session.execute(stmt)
#         return result.scalars().first()

#     def _generate_status_embed(self, battle):
#         """
#         Creates the PUBLIC Control Panel (Scores, Phase, Odds).
#         Does NOT contain the math breakdown.
#         """
#         color = discord.Color.gold()

#         # Title
#         if battle.battle_type == "SIEGE":
#             fief_name = battle.fief.name if battle.fief else "Unknown"
#             title = f"⚔️ Siege Command: {fief_name} (ID: {self.battle_id})"
#         else:
#             title = f"⚔️ Battle Command (ID: {self.battle_id})"

#         embed = discord.Embed(title=title, color=color)

#         # Names
#         att_name = battle.attacker.commander_name if battle.attacker else "Unknown"
#         def_name = battle.defender.commander_name if battle.defender else "Unknown"

#         embed.add_field(
#             name="Attacker",
#             value=f"**{att_name}**\nScore: {battle.attacker_score}/5",
#             inline=True,
#         )
#         embed.add_field(
#             name="Defender",
#             value=f"**{def_name}**\nScore: {battle.defender_score}/5",
#             inline=True,
#         )

#         if battle.siege_phase:
#             embed.add_field(name="Phase", value=battle.siege_phase, inline=False)

#         # Formatted Odds
#         odds = battle.current_odds
#         # Attacker 1-X, Defender X+1-100
#         def_start = odds + 1 if odds < 100 else 100

#         embed.add_field(
#             name="Battle Odds (First to 5)",
#             value=f"🔴 **Attacker:** 1-{odds}\n🔵 **Defender:** {def_start}-100",
#             inline=False,
#         )

#         embed.set_footer(text="GM: Use buttons below to progress the battle.")
#         return embed

#     async def generate_initial_embeds(self, session, calc_log=None):
#         """
#         Returns: (public_control_embed, private_calc_embed)
#         """
#         battle = await self._get_battle_fully_loaded(session)
#         if not battle:
#             return None, None

#         # 1. Public Control Panel
#         control_embed = self._generate_status_embed(battle)

#         # 2. Private Math Log
#         calc_embed = None
#         if calc_log:
#             calc_embed = discord.Embed(
#                 title=f"🧮 Odds Calculation (ID: {self.battle_id})",
#                 description=calc_log,
#                 color=discord.Color.blue(),
#             )

#         return control_embed, calc_embed

#     # --- BUTTONS ---

#     @discord.ui.button(
#         label="🎲 Roll Round",
#         style=discord.ButtonStyle.primary,
#         custom_id="battle_roll_round",
#     )
#     async def roll_round(
#         self, interaction: discord.Interaction, button: discord.ui.Button
#     ):
#         if not interaction.user.guild_permissions.administrator:
#             return await interaction.response.send_message(
#                 "❌ GM Only.", ephemeral=True
#             )

#         await interaction.response.defer()

#         async with get_session() as session:
#             service = BattleService(session)
#             battle = await self._get_battle_fully_loaded(session)
#             if not battle:
#                 return await interaction.followup.send(
#                     "Error: Battle not found.", ephemeral=True
#                 )

#             battle, roll_msg, winner, phase_transition = (
#                 await service.process_battle_round(self.battle_id)
#             )

#             if roll_msg == "Battle already finished.":
#                 for child in self.children:
#                     child.disabled = True
#                 await interaction.edit_original_response(
#                     content="**Battle Ended.**", view=self
#                 )
#                 return

#             # 1. Post Round Result to Channel (New Message)
#             # We post this to the channel where the button was clicked (Public)
#             if phase_transition:
#                 await interaction.channel.send(
#                     "🔥🔥 **BREACH!** The walls have fallen! The battle moves to the **STREETS**! 🔥🔥"
#                 )

#             round_embed = discord.Embed(
#                 description=roll_msg, color=discord.Color.dark_grey()
#             )
#             round_embed.set_author(
#                 name=f"Round Result ({battle.attacker_score}-{battle.defender_score})"
#             )
#             await interaction.channel.send(embed=round_embed)

#             # 2. Update the Control Panel (Edit existing message)
#             status_embed = self._generate_status_embed(battle)
#             await interaction.edit_original_response(embed=status_embed, view=self)

#             # 3. Handle Winner
#             if winner:
#                 final_report = await service.calculate_final_casualties(self.battle_id)
#                 final_embed = discord.Embed(
#                     title=f"🏁 Battle Concluded!",
#                     description=final_report,
#                     color=discord.Color.green(),
#                 )

#                 await interaction.channel.send(embed=final_embed)

#                 for child in self.children:
#                     child.disabled = True
#                 await interaction.edit_original_response(
#                     content="**Battle Ended.**", embed=status_embed, view=self
#                 )

#     @discord.ui.button(
#         label="Set Modifiers", style=discord.ButtonStyle.secondary, row=1
#     )
#     async def set_modifiers(
#         self, interaction: discord.Interaction, button: discord.ui.Button
#     ):
#         if not interaction.user.guild_permissions.administrator:
#             return await interaction.response.send_message("GM Only.", ephemeral=True)
#         modal = ModifierModal(self)
#         await interaction.response.send_modal(modal)

#     # Callback for Modal
#     # async def update_odds(
#     #     self, interaction: discord.Interaction, att_bonus: int, def_bonus: int
#     # ):
#     #     async with get_session() as session:
#     #         service = BattleService(session)
#     #         battle = await service.calculate_current_odds(
#     #             self.battle_id, att_bonus, def_bonus
#     #         )
#     #         if not battle:
#     #             return

#     #         # Refresh Panel
#     #         status_embed = self._generate_status_embed(battle)

#     #         # Since we are replying to the modal interaction, we edit the message attached to the view
#     #         await interaction.response.edit_message(embed=status_embed, view=self)

#     #         # Optional: Announce update in chat
#     #         # await interaction.channel.send(f"⚠️ **Odds Updated:** Attacker Bonus {att_bonus:+}, Defender Bonus {def_bonus:+}")

#     # async def update_odds(
#     #     self,
#     #     interaction: discord.Interaction,
#     #     att_bonus: int,
#     #     def_bonus: int,
#     #     att_cmd: int = None,
#     #     def_cmd: int = None,
#     # ):
#     #     async with get_session() as session:
#     #         service = BattleService(session)

#     #         # Pass all 4 arguments
#     #         battle = await service.calculate_current_odds(
#     #             self.battle_id, att_bonus, def_bonus, att_cmd, def_cmd
#     #         )

#     #         if not battle:
#     #             return

#     #         # Refresh Panel
#     #         gm_embed = self._generate_status_embed(battle)

#     #         # Show the GM what was applied
#     #         c_txt = ""
#     #         if att_cmd is not None or def_cmd is not None:
#     #             c_txt = f"\n👮 **Commanders Overridden:** Att({att_cmd if att_cmd else 'Auto'}) vs Def({def_cmd if def_cmd else 'Auto'})"

#     #         await interaction.response.edit_message(embed=gm_embed, view=self)
#     #         await interaction.followup.send(
#     #             f"✅ Odds updated! Target: **1-{battle.current_odds}**.{c_txt}",
#     #             ephemeral=True,
#     #         )

#     async def update_odds(
#         self,
#         interaction: discord.Interaction,
#         att_bonus: int,
#         def_bonus: int,
#         att_cmd: int = None,
#         def_cmd: int = None,
#     ):
#         async with get_session() as session:
#             service = BattleService(session)
#             battle = await service.calculate_current_odds(
#                 self.battle_id, att_bonus, def_bonus, att_cmd, def_cmd
#             )
#             if not battle:
#                 return

#             gm_embed = self._generate_status_embed(battle)

#             # FIX: Use edit_original_response (because modal deferred)
#             await interaction.edit_original_response(embed=gm_embed, view=self)

#             # Use followup for the confirmation message
#             await interaction.followup.send(
#                 f"✅ Odds updated! Target: **1-{battle.current_odds}**.", ephemeral=True
#             )

#     @discord.ui.button(
#         label="End Battle (Force)", style=discord.ButtonStyle.danger, row=1
#     )
#     async def end_battle(
#         self, interaction: discord.Interaction, button: discord.ui.Button
#     ):
#         if not interaction.user.guild_permissions.administrator:
#             return await interaction.response.send_message("GM Only.", ephemeral=True)
#         await interaction.response.defer()

#         async with get_session() as session:
#             battle = await self._get_battle_fully_loaded(session)
#             if not battle:
#                 return
#             service = BattleService(session)
#             report = await service.calculate_final_casualties(self.battle_id)
#             final_embed = discord.Embed(
#                 title=f"🛑 Battle Stopped by GM",
#                 description=report,
#                 color=discord.Color.blurple(),
#             )

#             await interaction.channel.send(embed=final_embed)

#             for child in self.children:
#                 child.disabled = True
#             status_embed = self._generate_status_embed(battle)
#             await interaction.edit_original_response(
#                 content="**Forced End.**", embed=status_embed, view=self
#             )


import discord
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.db_manager import get_session
from app.db.models import Battle, Army, House
from app.services.battle_service import BattleService
from app.ui.modals import ModifierModal


class BattleControlView(discord.ui.View):
    def __init__(self, battle_id: int):
        super().__init__(timeout=None)  # Persistent View
        self.battle_id = battle_id

    async def _get_battle_fully_loaded(self, session):
        stmt = (
            select(Battle)
            .where(Battle.id == self.battle_id)
            .options(
                selectinload(Battle.attacker),
                selectinload(Battle.defender),
                selectinload(Battle.fief),
                selectinload(Battle.game),
            )
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    def _generate_status_embed(self, battle):
        """
        Creates the PUBLIC Control Panel (Scores, Phase, Odds).
        Does NOT contain the math breakdown.
        """
        color = discord.Color.gold()

        # Title
        if battle.battle_type == "SIEGE":
            fief_name = battle.fief.name if battle.fief else "Unknown"
            title = f"⚔️ Siege Command: {fief_name} (ID: {self.battle_id})"
        else:
            title = f"⚔️ Battle Command (ID: {self.battle_id})"

        embed = discord.Embed(title=title, color=color)

        # Names
        att_name = battle.attacker.commander_name if battle.attacker else "Unknown"
        def_name = battle.defender.commander_name if battle.defender else "Unknown"

        embed.add_field(
            name="Attacker",
            value=f"**{att_name}**\nScore: {battle.attacker_score}/5",
            inline=True,
        )
        embed.add_field(
            name="Defender",
            value=f"**{def_name}**\nScore: {battle.defender_score}/5",
            inline=True,
        )

        if battle.siege_phase:
            embed.add_field(name="Phase", value=battle.siege_phase, inline=False)

        # Formatted Odds
        odds = battle.current_odds
        # Attacker 1-X, Defender X+1-100
        def_start = odds + 1 if odds < 100 else 100

        embed.add_field(
            name="Battle Odds (First to 5)",
            value=f"🔴 **Attacker:** 1-{odds}\n🔵 **Defender:** {def_start}-100",
            inline=False,
        )

        embed.set_footer(text="GM: Use buttons below to progress the battle.")
        return embed

    async def generate_initial_embeds(self, session, calc_log=None):
        """
        Returns: (public_control_embed, private_calc_embed)
        """
        battle = await self._get_battle_fully_loaded(session)
        if not battle:
            return None, None

        # 1. Public Control Panel
        control_embed = self._generate_status_embed(battle)

        # 2. Private Math Log
        calc_embed = None
        if calc_log:
            calc_embed = discord.Embed(
                title=f"🧮 Odds Calculation (ID: {self.battle_id})",
                description=calc_log,
                color=discord.Color.blue(),
            )

        return control_embed, calc_embed

    # --- BUTTONS ---

    @discord.ui.button(
        label="🎲 Roll Round",
        style=discord.ButtonStyle.primary,
        custom_id="battle_roll_round",
    )
    async def roll_round(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ GM Only.", ephemeral=True
            )

        await interaction.response.defer()

        async with get_session() as session:
            service = BattleService(session)
            # FIX: Ensure battle is loaded properly to prevent relationship errors
            battle = await self._get_battle_fully_loaded(session)
            if not battle:
                return await interaction.followup.send(
                    "Error: Battle not found.", ephemeral=True
                )

            # --- UPDATED UNPACKING: Now expects 6 values ---
            battle, roll_msg, winner, phase_transition, narration, casualties = (
                await service.process_battle_round(self.battle_id)
            )

            if roll_msg == "Battle already finished.":
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(
                    content="**Battle Ended.**", view=self
                )
                return

            # 1. Handle Phase Transitions (Siege)
            if phase_transition:
                await interaction.channel.send(
                    "🔥🔥 **BREACH!** The walls have fallen! The battle moves to the **STREETS**! 🔥🔥"
                )

            # 2. Build the Rich Report Embed (Result + Story + Stats)
            # Format Casualties strings
            att_loss_str = (
                f"🩸 -{casualties['attacker']}"
                if casualties and casualties["attacker"] > 0
                else "No Losses"
            )
            def_loss_str = (
                f"🩸 -{casualties['defender']}"
                if casualties and casualties["defender"] > 0
                else "No Losses"
            )

            # Combine mechanical result and AI narration
            description_text = f"{roll_msg}\n\n*{narration}*"

            round_embed = discord.Embed(
                title=f"⚔️ Round Analysis ({battle.attacker_score}-{battle.defender_score})",
                description=description_text,
                color=discord.Color.dark_red(),
            )
            round_embed.add_field(
                name="Attacker Losses", value=att_loss_str, inline=True
            )
            round_embed.add_field(
                name="Defender Losses", value=def_loss_str, inline=True
            )

            # Send the report as a new message in the channel
            await interaction.channel.send(embed=round_embed)

            # 3. Update the Control Panel (Edit existing message)
            status_embed = self._generate_status_embed(battle)
            await interaction.edit_original_response(embed=status_embed, view=self)

            # 4. Handle Winner
            if winner:
                final_report = await service.calculate_final_casualties(self.battle_id)
                final_embed = discord.Embed(
                    title=f"🏁 Battle Concluded!",
                    description=final_report,
                    color=discord.Color.green(),
                )

                await interaction.channel.send(embed=final_embed)

                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(
                    content="**Battle Ended.**", embed=status_embed, view=self
                )

    @discord.ui.button(
        label="Set Modifiers", style=discord.ButtonStyle.secondary, row=1
    )
    async def set_modifiers(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("GM Only.", ephemeral=True)
        modal = ModifierModal(self)
        await interaction.response.send_modal(modal)

    async def update_odds(
        self,
        interaction: discord.Interaction,
        att_bonus: int,
        def_bonus: int,
        att_cmd: int = None,
        def_cmd: int = None,
    ):
        async with get_session() as session:
            service = BattleService(session)
            battle = await service.calculate_current_odds(
                self.battle_id, att_bonus, def_bonus, att_cmd, def_cmd
            )
            if not battle:
                return

            gm_embed = self._generate_status_embed(battle)

            # FIX: Use edit_original_response (because modal deferred)
            await interaction.edit_original_response(embed=gm_embed, view=self)

            # Use followup for the confirmation message
            await interaction.followup.send(
                f"✅ Odds updated! Target: **1-{battle.current_odds}**.", ephemeral=True
            )

    @discord.ui.button(
        label="End Battle (Force)", style=discord.ButtonStyle.danger, row=1
    )
    async def end_battle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("GM Only.", ephemeral=True)
        await interaction.response.defer()

        async with get_session() as session:
            battle = await self._get_battle_fully_loaded(session)
            if not battle:
                return
            service = BattleService(session)
            report = await service.calculate_final_casualties(self.battle_id)
            final_embed = discord.Embed(
                title=f"🛑 Battle Stopped by GM",
                description=report,
                color=discord.Color.blurple(),
            )

            await interaction.channel.send(embed=final_embed)

            for child in self.children:
                child.disabled = True
            status_embed = self._generate_status_embed(battle)
            await interaction.edit_original_response(
                content="**Forced End.**", embed=status_embed, view=self
            )
