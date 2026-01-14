# # In app/ui/autobattle_view.py

# import discord
# from app.celery_app import celery_app
# from celery.result import AsyncResult


# class AutoBattleControlView(discord.ui.View):
#     def __init__(self, battle_id: int, resolver_task_id: str):
#         super().__init__(
#             timeout=960
#         )  # Timeout slightly longer than the 15-min grace period
#         self.battle_id = battle_id
#         self.resolver_task_id = resolver_task_id

#     async def disable_all_buttons(
#         self, interaction: discord.Interaction, response_text: str
#     ):
#         """Disables all buttons and updates the message."""
#         for item in self.children:
#             if isinstance(item, discord.ui.Button):
#                 item.disabled = True

#         # Edit the original message to show the action that was taken
#         original_embed = interaction.message.embeds[0]
#         original_embed.set_footer(text=f"Decision: {response_text}")
#         original_embed.color = discord.Color.dark_grey()
#         await interaction.message.edit(embed=original_embed, view=self)
#         self.stop()

#     @discord.ui.button(
#         label="Handle Manually (Cancel Auto-Battle)", style=discord.ButtonStyle.danger
#     )
#     async def cancel_autobattle(
#         self, interaction: discord.Interaction, button: discord.ui.Button
#     ):
#         if not interaction.user.guild_permissions.administrator:
#             return await interaction.response.send_message(
#                 "❌ GM Only.", ephemeral=True
#             )

#         # Revoke the scheduled Celery task for the first battle round
#         if self.resolver_task_id:
#             AsyncResult(self.resolver_task_id, app=celery_app).revoke(terminate=True)

#         await interaction.response.send_message(
#             "✅ **Auto-battle cancelled.** You may now resolve this battle manually using the `!battle` command.",
#             ephemeral=True,
#         )
#         await self.disable_all_buttons(interaction, "Auto-Battle Cancelled by GM.")

#     @discord.ui.button(
#         label="Let Auto-Battle Proceed", style=discord.ButtonStyle.success
#     )
#     async def proceed_autobattle(
#         self, interaction: discord.Interaction, button: discord.ui.Button
#     ):
#         if not interaction.user.guild_permissions.administrator:
#             return await interaction.response.send_message(
#                 "❌ GM Only.", ephemeral=True
#             )

#         await interaction.response.send_message(
#             "✅ **Auto-battle initiated!** The first round will now be processed.",
#             ephemeral=True,
#         )

#         # Immediately trigger the first round by calling the task directly
#         # We need to import the task here to avoid circular imports
#         from app.tasks.battle_tasks import run_auto_battle_round

#         run_auto_battle_round.delay(self.battle_id, 1)  # Start with round 1

#         await self.disable_all_buttons(interaction, "Auto-Battle Initiated by GM.")


# In app/ui/autobattle_view.py

import discord
from app.celery_app import celery_app
from celery.result import AsyncResult
from app.db.db_manager import get_session # <--- Add this
from app.db.models import Battle          # <--- Add this
from sqlalchemy import delete             # <--- Add this


class AutoBattleControlView(discord.ui.View):
    def __init__(self, battle_id: int, resolver_task_id: str):
        super().__init__(timeout=960)  # 16 mins (Grace period + buffer)
        self.battle_id = battle_id
        self.resolver_task_id = resolver_task_id

    async def disable_all_buttons(
        self, interaction: discord.Interaction, response_text: str
    ):
        """Disables all buttons and updates the message."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        # Edit the original message to show the action that was taken
        original_embed = interaction.message.embeds[0]
        original_embed.set_footer(text=f"Decision: {response_text}")
        original_embed.color = discord.Color.dark_grey()

        # Use edit_original_response if already deferred/responded, otherwise message.edit
        try:
            await interaction.message.edit(embed=original_embed, view=self)
        except:
            pass  # Interaction flow might vary, safe pass
        self.stop()

    @discord.ui.button(
        label="Handle Manually (Cancel Auto-Battle)", style=discord.ButtonStyle.danger
    )
    async def cancel_autobattle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ GM Only.", ephemeral=True
            )

        # 1. Revoke the scheduled Celery task so it doesn't run later
        if self.resolver_task_id:
            AsyncResult(self.resolver_task_id, app=celery_app).revoke(terminate=True)

        # 2. Delete the "Auto" version so you can start a "Manual" version cleanly.
        try:
            async with get_session() as session:
                await session.execute(
                    delete(Battle).where(Battle.id == self.battle_id)
                )
                await session.commit()
            
            msg = (
                "✅ **Auto-battle cancelled and record deleted.**\n"
                "You may now use:\n"
                "1. `!battle [AttackerID] [DefenderID]` to roll manually.\n"
                "2. `!gm_war calc_casualties` to skip rolling entirely."
            )
        except Exception as e:
            msg = f"⚠️ Task cancelled, but DB deletion failed: {e}. You might have duplicate battles if you run !battle now."

        # FIX: Send the 'msg' variable, not the hardcoded string
        await interaction.response.send_message(msg, ephemeral=True)
        
        await self.disable_all_buttons(interaction, "Auto-Battle Cancelled by GM.")

    @discord.ui.button(
        label="Let Auto-Battle Proceed", style=discord.ButtonStyle.success
    )
    async def proceed_autobattle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ GM Only.", ephemeral=True
            )

        # --- FIX START ---
        # 1. Revoke the WAITING scheduled task so we don't run round 1 twice
        if self.resolver_task_id:
            AsyncResult(self.resolver_task_id, app=celery_app).revoke(terminate=True)
        # --- FIX END ---

        await interaction.response.send_message(
            "✅ **Auto-battle initiated!** The first round will now be processed immediately.",
            ephemeral=True,
        )

        # 2. Immediately trigger the first round
        from app.tasks.battle_tasks import run_auto_battle_round

        run_auto_battle_round.delay(self.battle_id, 1)  # Start with round 1

        await self.disable_all_buttons(interaction, "Auto-Battle Initiated by GM.")
