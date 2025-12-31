# In app/ui/interaction_view.py

import discord


class InteractionView(discord.ui.View):
    def __init__(self, interaction_id: int, for_army_id: int):
        # We set a long timeout because the decision window is handled by our backend timer.
        super().__init__(timeout=3600 * 2)  # 2 hour timeout

        # --- CRITICAL ---
        # The custom_id must be unique and contain all the info we need.
        # Format: {prefix}_{action}_{interaction_id}_{army_id_making_the_choice}

        self.battle_button = discord.ui.Button(
            label="Declare Hostile Intent (Battle)",
            style=discord.ButtonStyle.danger,
            custom_id=f"interaction_BATTLE_{interaction_id}_{for_army_id}",
            emoji="⚔️",
        )

        self.meeting_button = discord.ui.Button(
            label="Request Parley (Meeting)",
            style=discord.ButtonStyle.primary,
            custom_id=f"interaction_MEETING_{interaction_id}_{for_army_id}",
            emoji="🤝",
        )

        self.march_on_button = discord.ui.Button(
            label="Continue March",
            style=discord.ButtonStyle.secondary,
            custom_id=f"interaction_MARCH_ON_{interaction_id}_{for_army_id}",
        )

        self.add_item(self.battle_button)
        self.add_item(self.meeting_button)
        self.add_item(self.march_on_button)

    async def disable_all_buttons(self):
        """Disables all buttons, typically after a choice is made."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
