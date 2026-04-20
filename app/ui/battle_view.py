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
        for child in self.children:
            if getattr(child, "custom_id", None) == "battle_roll_round":
                child.label = "Resolve Phase"

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

        if battle.battle_type == "SIEGE":
            embed.add_field(
                name="Attacker",
                value=f"**{att_name}**\nBesieging Force",
                inline=True,
            )
            embed.add_field(
                name="Defender",
                value=f"**{def_name}**\nGarrison",
                inline=True,
            )
        else:
            embed.add_field(
                name="Attacker",
                value=f"**{att_name}**\nPhase Wins: {battle.attacker_score}/5",
                inline=True,
            )
            embed.add_field(
                name="Defender",
                value=f"**{def_name}**\nPhase Wins: {battle.defender_score}/5",
                inline=True,
            )
        if battle.siege_phase:
            embed.add_field(name="Phase", value=battle.siege_phase, inline=False)
        else:
            embed.add_field(
                name="Phase",
                value=getattr(battle, "phase", None) or "ROUND",
                inline=False,
            )

        turn_label = "Turn" if battle.battle_type == "SIEGE" else "Round"
        state_lines = [
            f"{turn_label}: {getattr(battle, 'round_number', 0) or 0}",
            f"Terrain: {getattr(battle, 'terrain', None) or 'unknown'}",
            f"Morale: Attacker {getattr(battle, 'attacker_morale', 100) or 100} / Defender {getattr(battle, 'defender_morale', 100) or 100}",
            f"Supply: Attacker {getattr(battle, 'attacker_supply', 100) or 100} / Defender {getattr(battle, 'defender_supply', 100) or 100}",
        ]
        if battle.battle_type == "SIEGE":
            wall_integrity = getattr(battle, "wall_integrity", None)
            state_lines.append(
                f"Walls: {wall_integrity if wall_integrity is not None else 100}"
            )
            state_lines.append(
                f"Actions: Attacker {getattr(battle, 'attacker_plan', None) or 'invest'} / Defender {getattr(battle, 'defender_plan', None) or 'ration'}"
            )
            if getattr(battle, "blockade_fleet_id", None):
                state_lines.append(f"Blockade Fleet: {battle.blockade_fleet_id}")
        else:
            state_lines.append(
                f"Plans: Attacker {getattr(battle, 'attacker_plan', None) or 'cautious'} / Defender {getattr(battle, 'defender_plan', None) or 'cautious'}"
            )
        embed.add_field(name="Battle State", value="\n".join(state_lines), inline=False)

        odds, def_start = battle.current_odds, (battle.current_odds + 1)
        odds_label = (
            "Street Fighting Outlook"
            if battle.battle_type == "SIEGE"
            else "Battle Odds (Current Phase)"
        )
        embed.add_field(
            name=odds_label,
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
            if battle.battle_type == "SIEGE":
                report_title = (
                    f"Siege Turn {getattr(battle, 'round_number', 0) or ''}"
                )
            else:
                report_title = (
                    f"Phase Analysis ({battle.attacker_score}-{battle.defender_score})"
                )
            round_embed = discord.Embed(
                title=report_title,
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

            gm_audit = casualties.get("_gm_audit") if casualties else None
            if gm_audit and getattr(battle, "gm_channel_id", None):
                gm_channel = interaction.client.get_channel(battle.gm_channel_id)
                if gm_channel:
                    await gm_channel.send(
                        embed=discord.Embed(
                            title=f"Phase Odds Audit (Battle ID {self.battle_id})",
                            description=gm_audit,
                            color=discord.Color.blue(),
                        )
                    )

            # Reload the battle with all relationships loaded before generating the status embed.
            battle = await self._get_battle_fully_loaded(session)

            status_embed = self._generate_status_embed(battle)
            await interaction.edit_original_response(embed=status_embed, view=self)

            # --- HANDLE WINNER ---
            if winner:
                # FIX: Added an extra underscore to unpack 3 values (report, guild_id, notif_data)
                final_report, _, notif_data = await service.resolve_manual_battle_aftermath(
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
                aftermath_audit = (notif_data or {}).get("_gm_audit")
                if aftermath_audit and getattr(battle, "gm_channel_id", None):
                    gm_channel = interaction.client.get_channel(battle.gm_channel_id)
                    if gm_channel:
                        await gm_channel.send(
                            embed=discord.Embed(
                                title=f"Aftermath Audit (Battle ID {self.battle_id})",
                                description=aftermath_audit,
                                color=discord.Color.blue(),
                            )
                        )

                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(
                    content="**Battle Ended.**", view=self
                )

    @discord.ui.button(
        label="Fast Resolve", style=discord.ButtonStyle.success, row=1
    )
    async def fast_resolve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("GM Only.", ephemeral=True)
        await interaction.response.defer()

        async with get_session() as session:
            service = BattleService(session)
            battle, reports, winner = await service.fast_resolve_battle(self.battle_id)

            if not battle:
                return await interaction.followup.send(
                    "Error: Battle not found.", ephemeral=True
                )

            for report in reports:
                casualties = report.get("casualties") or {}
                phase_embed = discord.Embed(
                    title=f"Fast Resolve: {report.get('phase', 'Phase')}",
                    description=f"{report['roll_msg']}\n\n*{report['narration']}*",
                    color=discord.Color.dark_red(),
                )
                phase_embed.add_field(
                    name="Attacker Losses",
                    value=(
                        f"Losses: {casualties.get('attacker', 0)}"
                        if casualties.get("attacker", 0) > 0
                        else "No Losses"
                    ),
                    inline=True,
                )
                phase_embed.add_field(
                    name="Defender Losses",
                    value=(
                        f"Losses: {casualties.get('defender', 0)}"
                        if casualties.get("defender", 0) > 0
                        else "No Losses"
                    ),
                    inline=True,
                )
                await interaction.channel.send(embed=phase_embed)
                gm_audit = casualties.get("_gm_audit") if casualties else None
                if gm_audit and getattr(battle, "gm_channel_id", None):
                    gm_channel = interaction.client.get_channel(battle.gm_channel_id)
                    if gm_channel:
                        await gm_channel.send(
                            embed=discord.Embed(
                                title=(
                                    f"Fast Resolve Odds Audit "
                                    f"(Battle ID {self.battle_id})"
                                ),
                                description=gm_audit,
                                color=discord.Color.blue(),
                            )
                        )

            battle = await self._get_battle_fully_loaded(session)
            await interaction.edit_original_response(
                embed=self._generate_status_embed(battle), view=self
            )

            if winner:
                final_report, _, notif_data = await service.resolve_manual_battle_aftermath(
                    self.battle_id
                )
                final_embed = discord.Embed(
                    title="Battle Concluded!",
                    description=final_report,
                    color=discord.Color.green(),
                )
                await interaction.channel.send(embed=final_embed)
                aftermath_audit = (notif_data or {}).get("_gm_audit")
                if aftermath_audit and getattr(battle, "gm_channel_id", None):
                    gm_channel = interaction.client.get_channel(battle.gm_channel_id)
                    if gm_channel:
                        await gm_channel.send(
                            embed=discord.Embed(
                                title=f"Aftermath Audit (Battle ID {self.battle_id})",
                                description=aftermath_audit,
                                color=discord.Color.blue(),
                            )
                        )

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
            result = await service.calculate_current_odds(
                self.battle_id, att_bonus, def_bonus, att_cmd, def_cmd
            )
            if not result:
                return
            battle, calc_log = result

            battle = await self._get_battle_fully_loaded(session)

            await interaction.edit_original_response(
                embed=self._generate_status_embed(battle), view=self
            )
            if calc_log and getattr(battle, "gm_channel_id", None):
                gm_channel = interaction.client.get_channel(battle.gm_channel_id)
                if gm_channel:
                    await gm_channel.send(
                        embed=discord.Embed(
                            title=f"Odds Recalculation Audit (Battle ID {self.battle_id})",
                            description=calc_log,
                            color=discord.Color.blue(),
                        )
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
            battle = await self._get_battle_fully_loaded(session)
            no_combat = (
                battle
                and (battle.round_number or 0) == 0
                and (battle.attacker_score or 0) == 0
                and (battle.defender_score or 0) == 0
            )
            if no_combat:
                report, _, notif_data = await service.cancel_battle_without_aftermath(
                    self.battle_id
                )
            else:
                report, _, notif_data = await service.resolve_manual_battle_aftermath(
                    self.battle_id
                )

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
            gm_audit = (notif_data or {}).get("_gm_audit")
            if gm_audit and battle and getattr(battle, "gm_channel_id", None):
                gm_channel = interaction.client.get_channel(battle.gm_channel_id)
                if gm_channel:
                    await gm_channel.send(
                        embed=discord.Embed(
                            title=(
                                f"Forced End Audit (Battle ID {self.battle_id})"
                                if no_combat
                                else f"Aftermath Audit (Battle ID {self.battle_id})"
                            ),
                            description=gm_audit,
                            color=discord.Color.blue(),
                        )
                    )

            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(
                content="**Forced End.**", view=self
            )
