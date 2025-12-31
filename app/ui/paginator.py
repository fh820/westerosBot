import discord


class Paginator(discord.ui.View):
    def __init__(self, embeds: list[discord.Embed]):
        super().__init__(timeout=180)
        self.embeds = embeds
        self.current_page = 0
        for i, embed in enumerate(self.embeds):
            embed.set_footer(text=f"Page {i+1} of {len(self.embeds)}")

    async def show_page(self, interaction: discord.Interaction, page_number: int):
        self.current_page = page_number
        embed = self.embeds[self.current_page]
        self.children[0].disabled = self.current_page == 0
        self.children[1].disabled = self.current_page == len(self.embeds) - 1
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="◀ Previous", style=discord.ButtonStyle.secondary, disabled=True
    )
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.show_page(interaction, self.current_page - 1)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.show_page(interaction, self.current_page + 1)
