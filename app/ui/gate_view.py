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
        # --- CRITICAL FIX: Initialize message to None ---
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if self.defender_discord_id and interaction.user.id == self.defender_discord_id:
            return True

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

        payload = {
            "type": "GATE_RESPONSE",
            "guild_id": self.guild_id,
            "army_id": self.army_id,
            "action": "GRANT",
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
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
        # Robust title update:
        gate_name = embed.title.split("Gate Alert: ")[-1]
        embed.title = f"❌ HALTED: {gate_name}"
        embed.set_footer(
            text=f"Decision by {interaction.user.display_name}: Passage Denied"
        )

        payload = {
            "type": "PASSAGE_DENIED",
            "guild_id": self.guild_id,
            "army_id": self.army_id,
            "gate_name": gate_name,
            "denied_by": interaction.user.display_name,
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        """
        Safely handles view timeout.
        FIXED: Uses hasattr/getattr to prevent AttributeError crashes.
        """
        # Safe attribute check
        msg = getattr(self, "message", None)
        if not msg:
            return

        try:
            # Refresh message state
            msg = await msg.channel.fetch_message(msg.id)
            embed = msg.embeds[0]

            # If already decided, stop.
            if embed.footer.text and "Decision by" in embed.footer.text:
                return

            self.disable_all_buttons()
            gate_name = embed.title.split("Gate Alert: ")[-1]
            embed.color = discord.Color.dark_red()
            embed.title = f"❌ TIMED OUT: {gate_name}"
            embed.set_footer(
                text="No decision was made in time. Passage has been denied by default."
            )

            payload = {
                "type": "PASSAGE_DENIED",
                "guild_id": self.guild_id,
                "army_id": self.army_id,
                "gate_name": gate_name,
                "denied_by": "Default Gate Protocol",
            }
            REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

            await msg.edit(embed=embed, view=self)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            # Message was deleted or channel is gone, just stop the view.
            pass
        except Exception as e:
            print(f"Error in GateActionView timeout: {e}")
