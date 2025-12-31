import discord
from app.services.economy import EconomyService
from app.db.db_manager import get_session


class TransactionView(discord.ui.View):
    def __init__(
        self,
        source_house_id: int,
        target_category: str,
        target_id: int,
        amount: int,
        approver_discord_id: int = None,
        is_gm_approval: bool = False,
    ):
        super().__init__(timeout=600)
        self.source_house_id = source_house_id
        self.target_category = target_category  # "ARMY" or "HOUSE"
        self.target_id = target_id
        self.amount = amount
        self.approver_discord_id = approver_discord_id
        self.is_gm_approval = is_gm_approval

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.is_gm_approval:
            if interaction.user.guild_permissions.administrator:
                return True
            await interaction.response.send_message(
                "❌ Only a GM can approve this.", ephemeral=True
            )
            return False

        if self.approver_discord_id and interaction.user.id != self.approver_discord_id:
            await interaction.response.send_message(
                "❌ This confirmation is not for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Accept Transfer", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        async with get_session() as session:
            service = EconomyService(session)
            success, msg = await service.execute_transfer(
                self.source_house_id, self.target_category, self.target_id, self.amount
            )

            if success:
                embed = discord.Embed(
                    title="💸 Transfer Complete",
                    description=msg,
                    color=discord.Color.green(),
                )
                # Use message.edit if edit_original_response fails (sometimes happens with views)
                try:
                    await interaction.edit_original_response(embed=embed, view=None)
                except:
                    await interaction.message.edit(embed=embed, view=None)
            else:
                await interaction.followup.send(msg, ephemeral=True)

        self.stop()

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.edit(
            content="❌ **Transaction Rejected.**", view=None
        )
        self.stop()
