import discord


class SimplePaginationView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.current_page = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only allow the user who invoked the command to use the buttons
        return (
            interaction.user == self.message.interaction.user
            if self.message.interaction
            else True
        )

    def _update_buttons(self):
        self.first_page.disabled = self.current_page == 0
        self.prev_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page == len(self.pages) - 1
        self.last_page.disabled = self.current_page == len(self.pages) - 1

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, disabled=True)
    async def first_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.current_page = 0
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current_page], view=self
        )

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary, disabled=True)
    async def prev_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.current_page -= 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current_page], view=self
        )

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.current_page += 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current_page], view=self
        )

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def last_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.current_page = len(self.pages) - 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current_page], view=self
        )
