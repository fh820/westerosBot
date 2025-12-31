import discord


class ModifierModal(discord.ui.Modal, title="Battle Modifiers"):
    att_bonus = discord.ui.TextInput(
        label="Attacker Bonus (+/-)",
        placeholder="e.g. 10 for reinforcements, -5 for rain",
        default="0",
        required=True,
    )
    def_bonus = discord.ui.TextInput(
        label="Defender Bonus (+/-)",
        placeholder="e.g. 20 for high ground",
        default="0",
        required=True,
    )

    att_cmd = discord.ui.TextInput(
        label="Attacker Commander Score",
        placeholder="Leave empty to use Auto-Detect",
        required=False,
    )
    def_cmd = discord.ui.TextInput(
        label="Defender Commander Score",
        placeholder="Leave empty to use Auto-Detect",
        required=False,
    )

    def __init__(self, view):
        super().__init__()
        self.view = view

    # async def on_submit(self, interaction: discord.Interaction):
    #     try:
    #         a_val = int(self.att_bonus.value)
    #         d_val = int(self.def_bonus.value)
    #     except ValueError:
    #         return await interaction.response.send_message(
    #             "❌ Please enter numbers only.", ephemeral=True
    #         )

    #     await self.view.update_odds(interaction, a_val, d_val)

    # async def on_submit(self, interaction: discord.Interaction):
    #     try:
    #         # Parse Bonuses (Default 0)
    #         a_val = int(self.att_bonus.value) if self.att_bonus.value else 0
    #         d_val = int(self.def_bonus.value) if self.def_bonus.value else 0

    #         # Parse Commanders (None if empty)
    #         a_cmd = int(self.att_cmd.value) if self.att_cmd.value.strip() else None
    #         d_cmd = int(self.def_cmd.value) if self.def_cmd.value.strip() else None

    #     except ValueError:
    #         return await interaction.response.send_message(
    #             "❌ Please enter valid numbers.", ephemeral=True
    #         )

    #     # Send all 4 values to the view
    #     await self.view.update_odds(interaction, a_val, d_val, a_cmd, d_cmd)

    async def on_submit(self, interaction: discord.Interaction):
        # 1. Defer immediately to prevent 404/Timeout
        await interaction.response.defer()

        try:
            a_val = int(self.att_bonus.value) if self.att_bonus.value else 0
            d_val = int(self.def_bonus.value) if self.def_bonus.value else 0
            a_cmd = int(self.att_cmd.value) if self.att_cmd.value else None
            d_cmd = int(self.def_cmd.value) if self.def_cmd.value else None
        except ValueError:
            return await interaction.followup.send("❌ Numbers only.", ephemeral=True)

        await self.view.update_odds(interaction, a_val, d_val, a_cmd, d_cmd)
