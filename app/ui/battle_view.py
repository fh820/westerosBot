# In app/ui/battle_view.py

import discord
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.db_manager import get_session
from app.db.models import Battle
from app.services.battle_service import BattleService
from app.ui.modals import ModifierModal


class BattleControlView(discord.ui.View):
    def __init__(self, battle_id: int):
        super().__init__(timeout=None)
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
        return (await session.execute(stmt)).scalars().first()

    def _generate_status_embed(self, battle):
        title = f"⚔️ Battle Command (ID: {self.battle_id})"
        if battle.battle_type == "SIEGE":
            title = f"⚔️ Siege Command: {battle.fief.name if battle.fief else ''} (ID: {self.battle_id})"

        embed = discord.Embed(title=title, color=discord.Color.gold())

        # Safe access now that we ensure it's loaded
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

        odds, def_start = battle.current_odds, (battle.current_odds + 1)
        embed.add_field(
            name="Battle Odds (First to 5)",
            value=f"🔴 Attacker: 1-{odds}\n🔵 Defender: {def_start}-100",
            inline=False,
        )
        embed.set_footer(text="GM: Use buttons below to progress the battle.")
        return embed

    async def generate_initial_embeds(self, session, calc_log=None):
        battle = await self._get_battle_fully_loaded(session)
        if not battle:
            return None, None
        control_embed = self._generate_status_embed(battle)
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
            battle, roll_msg, winner, phase_transition, narration, casualties = (
                await service.process_battle_round(self.battle_id)
            )

            if not battle:
                return await interaction.followup.send(
                    "Error: Battle not found.", ephemeral=True
                )
            if roll_msg == "Battle already finished.":
                return

            if phase_transition:
                await interaction.channel.send(
                    "🔥🔥 **BREACH!** The walls have fallen! The battle moves to the **STREETS**! 🔥🔥"
                )

            # Round Report
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
            await interaction.channel.send(embed=round_embed)

            # --- CRITICAL FIX START ---
            # Reload the battle with all relationships loaded before generating the status embed.
            # This prevents the 'MissingGreenlet' error when accessing battle.attacker/defender.
            battle = await self._get_battle_fully_loaded(session)
            # --- CRITICAL FIX END ---

            status_embed = self._generate_status_embed(battle)
            await interaction.edit_original_response(embed=status_embed, view=self)

            # --- HANDLE WINNER ---
            if winner:
                final_report, _ = await service.resolve_manual_battle_aftermath(
                    self.battle_id
                )

                if final_report.startswith("Error"):
                    final_embed = discord.Embed(
                        title="❌ Resolution Error",
                        description=final_report,
                        color=discord.Color.red(),
                    )
                else:
                    final_embed = discord.Embed(
                        title="🏁 Battle Concluded!",
                        description=final_report,
                        color=discord.Color.green(),
                    )

                await interaction.channel.send(embed=final_embed)

                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(
                    content="**Battle Ended.**", view=self
                )

    @discord.ui.button(
        label="Set Modifiers", style=discord.ButtonStyle.secondary, row=1
    )
    async def set_modifiers(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("GM Only.", ephemeral=True)
        await interaction.response.send_modal(ModifierModal(self))

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

            # --- OPTIONAL SAFETY FIX ---
            # Reload to ensure view doesn't crash on render
            battle = await self._get_battle_fully_loaded(session)

            await interaction.edit_original_response(
                embed=self._generate_status_embed(battle), view=self
            )
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
            service = BattleService(session)
            report, _ = await service.resolve_manual_battle_aftermath(self.battle_id)

            if report.startswith("Error"):
                final_embed = discord.Embed(
                    title="❌ Resolution Error",
                    description=report,
                    color=discord.Color.red(),
                )
            else:
                final_embed = discord.Embed(
                    title="🛑 Battle Stopped by GM",
                    description=report,
                    color=discord.Color.blurple(),
                )
            await interaction.channel.send(embed=final_embed)

            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(
                content="**Forced End.**", view=self
            )
