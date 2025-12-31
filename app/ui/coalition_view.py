# In app/ui/coalition_view.py

import discord
from discord.ui import View, Button
from app.db.db_manager import get_session
from app.services.warfare_service import WarfareService


class CoalitionConsentView(View):
    def __init__(self, bot, initiator, targets_map, game_id, new_name, army_ids):
        super().__init__(timeout=86400)  # 24-hour timeout
        self.bot = bot
        self.initiator = initiator
        self.targets_map = targets_map  # {discord.Member: [Army, ...]}
        self.game_id = game_id
        self.new_name = new_name
        self.army_ids = army_ids

        # State tracking: The initiator automatically accepts.
        self.accepted = {self.initiator.id}

    def create_embed(self, status_message=None):
        """Creates the embed showing the proposal status."""
        embed = discord.Embed(
            title="🤝 Coalition Proposal",
            description=f"**{self.initiator.display_name}** proposes forming a new coalition named **{self.new_name}**.",
            color=discord.Color.gold(),
        )

        status_lines = []
        # Create a sorted list of members for consistent display order
        sorted_members = sorted(self.targets_map.keys(), key=lambda m: m.display_name)

        for member in sorted_members:
            armies = self.targets_map[member]
            icon = "✅" if member.id in self.accepted else "🟡"
            army_list = ", ".join(
                [f"{a.commander_name} ({a.troop_count})" for a in armies]
            )
            status_lines.append(
                f"{icon} **{member.display_name}** contributes: {army_list}"
            )

        embed.add_field(
            name="Participants & Contributions",
            value="\n".join(status_lines),
            inline=False,
        )

        if status_message:
            embed.set_footer(text=status_message)
        else:
            embed.set_footer(text="All participants must click 'Accept' to proceed.")

        return embed

    @discord.ui.button(
        label="Accept", style=discord.ButtonStyle.success, custom_id="coalition_accept"
    )
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        # Check if the interactor is a participant
        participant_ids = {member.id for member in self.targets_map.keys()}
        if interaction.user.id not in participant_ids:
            return await interaction.response.send_message(
                "You are not a participant in this proposal.", ephemeral=True
            )

        self.accepted.add(interaction.user.id)

        # Check if all participants have now accepted
        if self.accepted == participant_ids:
            self.stop()  # Stop the view from listening

            async with get_session() as session:
                service = WarfareService(session)
                # Call the service, bypassing the authority check because we have consent
                success, msg = await service.form_coalition(
                    self.game_id,
                    self.initiator.id,
                    self.new_name,
                    tuple(self.army_ids),
                    bypass_auth=True,
                )

            final_embed = self.create_embed(
                status_message=msg if success else f"Error: {msg}"
            )
            for item in self.children:  # Disable buttons
                item.disabled = True
            await interaction.response.edit_message(embed=final_embed, view=self)
        else:
            # Update the embed to show the new acceptance
            await interaction.response.edit_message(embed=self.create_embed())

    @discord.ui.button(
        label="Decline", style=discord.ButtonStyle.danger, custom_id="coalition_decline"
    )
    async def decline_button(self, interaction: discord.Interaction, button: Button):
        participant_ids = {member.id for member in self.targets_map.keys()}
        if interaction.user.id not in participant_ids:
            return await interaction.response.send_message(
                "You are not a participant in this proposal.", ephemeral=True
            )

        self.stop()
        final_embed = self.create_embed(
            status_message=f"❌ Proposal declined by {interaction.user.display_name}."
        )
        for item in self.children:  # Disable buttons
            item.disabled = True
        await interaction.response.edit_message(embed=final_embed, view=self)
