import discord


class ProposalView(discord.ui.View):
    """
    A flexible view for handling proposals that require consent.
    Handles Player-to-Player, Player-to-GM, and self-acceptance scenarios.
    """

    def __init__(
        self,
        *,
        initiator: discord.Member,
        consenter: discord.Member,
        action_name: str,
        proposal_embed: discord.Embed,
        on_accept_callback,
        is_gm_approval: bool = False,
    ):
        super().__init__(timeout=86400)  # 24-hour timeout for proposals
        self.initiator = initiator
        self.consenter = consenter
        self.action_name = action_name
        self.proposal_embed = proposal_embed
        self.on_accept_callback = on_accept_callback
        self.is_gm_approval = is_gm_approval

        # If the initiator is also the sole consenter, there's nothing to wait for.
        if not is_gm_approval and initiator.id == consenter.id:
            self.value = True  # Auto-accept
            self.stop()

        self.value = None  # Represents a pending decision

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Check if the user clicking is authorized to do so.
        is_authorized = (
            # Is the user the specific person who needs to consent?
            interaction.user.id == self.consenter.id
            or
            # Or is this a GM approval, and the user is an admin?
            (self.is_gm_approval and interaction.user.guild_permissions.administrator)
        )

        if not is_authorized:
            await interaction.response.send_message(
                "This proposal is not for you to decide.", ephemeral=True
            )
            return

        self.value = True
        self.stop()  # Stop listening for further interactions

        # Disable all buttons to show a decision was made
        for child in self.children:
            child.disabled = True

        # Acknowledge the click immediately and show a processing message
        await interaction.response.edit_message(embed=self.proposal_embed, view=self)

        # Run the actual game logic (e.g., updating the database)
        await self.on_accept_callback(interaction)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Check for authorization to decline
        is_authorized = (
            interaction.user.id == self.consenter.id
            or interaction.user.id == self.initiator.id  # The initiator can also cancel
            or (
                self.is_gm_approval and interaction.user.guild_permissions.administrator
            )
        )

        if not is_authorized:
            await interaction.response.send_message(
                "You cannot decline this proposal.", ephemeral=True
            )
            return

        self.value = False
        self.stop()

        for child in self.children:
            child.disabled = True

        # Update the original embed with a clear "Declined" message
        self.proposal_embed.color = discord.Color.red()
        self.proposal_embed.set_footer(
            text=f"❌ Declined by {interaction.user.display_name}."
        )
        await interaction.response.edit_message(embed=self.proposal_embed, view=self)
