import discord
import json
import redis
import os

REDIS_CLIENT = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


class GateActionView(discord.ui.View):
    def __init__(
        self,
        bot,
        guild_id: int,
        army_id: int,
        defender_discord_id: int | None,
        timeout: int = 3600,
    ):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.guild_id = guild_id
        self.army_id = army_id
        self.defender_discord_id = defender_discord_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if self.defender_discord_id and interaction.user.id == self.defender_discord_id:
            return True
        if (
            not self.defender_discord_id
            and interaction.user.guild_permissions.administrator
        ):
            return True  # Let admins respond for NPCs

        await interaction.response.send_message(
            "You do not have permission to control this gate.", ephemeral=True
        )
        return False

    def disable_all_buttons(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(
        label="Grant Passage",
        style=discord.ButtonStyle.green,
        custom_id="grant_passage",
    )
    async def grant_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.disable_all_buttons()
        button.label = "Passage Granted"

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.set_footer(
            text=f"Decision by {interaction.user.display_name}: Passage Granted"
        )

        # --- KEY CHANGE: GRANT NOW SENDS THE REDIS EVENT ---
        # This tells the backend to resume the army's march from its original destination.
        payload = {
            "type": "GATE_RESPONSE",
            "guild_id": self.guild_id,
            "army_id": self.army_id,
            "action": "GRANT",  # Specifically granting passage
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
        print(f"📡 Published GATE_RESPONSE (GRANT) for army {self.army_id} to Redis.")

        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="Deny Passage", style=discord.ButtonStyle.red, custom_id="deny_passage"
    )
    async def deny_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.disable_all_buttons()
        button.label = "Passage Denied"

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.dark_red()
        embed.title = f"❌ HALTED: {embed.title}"
        embed.set_footer(
            text=f"Decision by {interaction.user.display_name}: Passage Denied"
        )

        # --- NEW: PUBLISH NOTIFICATION FOR THE ATTACKER ---
        payload = {
            "type": "PASSAGE_DENIED",
            "guild_id": self.guild_id,
            "army_id": self.army_id,
            "gate_name": embed.title.split("HALTED: ⚔️ Gate Alert: ")[
                -1
            ],  # Extract gate name from title
            "denied_by": interaction.user.display_name,
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
        print(
            f"📡 Published PASSAGE_DENIED notification for army {self.army_id} to Redis."
        )
        # --- END OF NEW CODE ---

        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        """
        Executes when the view's timeout expires (e.g., after 1 hour).
        Defaults to DENYING passage.
        """
        # The original message is stored on the view object
        if not self.message:
            return

        # Get the original embed to modify it
        embed = self.message.embeds[0]

        # Check if a button has already been clicked. If so, do nothing.
        # We can check by seeing if the footer text was changed.
        if "Decision by" in embed.footer.text:
            return

        print(f"Gate Action for Army {self.army_id} timed out. Defaulting to DENY.")

        self.disable_all_buttons()
        embed.color = discord.Color.dark_red()
        embed.title = f"❌ TIMED OUT: {embed.title.replace('⚔️ Gate Alert: ', '')}"
        embed.set_footer(
            text="No decision was made in time. Passage has been denied by default."
        )

        # Publish the same "PASSAGE_DENIED" event to notify the attacker
        payload = {
            "type": "PASSAGE_DENIED",
            "guild_id": self.guild_id,
            "army_id": self.army_id,
            "gate_name": embed.title.split("TIMED OUT: ")[-1],
            "denied_by": "Default Gate Protocol",  # A flavorful name for the denier
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

        # Edit the original message to show the final state
        await self.message.edit(embed=embed, view=self)
