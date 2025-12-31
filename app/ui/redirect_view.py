# app/ui/redirect_view.py
import discord
from discord.ext import commands

from app.db.db_manager import get_session
from app.db.repositories import ArmyRepo
from app.services.warfare_service import WarfareService
from app.db.models import User, Army
from app.tasks.light_tasks import resolve_army_arrival
from sqlalchemy import select
from app.db.repositories import GameRepo, ArmyRepo


# --- THE REDIRECT MODAL ---
class RedirectModal(discord.ui.Modal, title="Issue Redirect Orders"):
    def __init__(self, bot: commands.Bot, army: Army):
        super().__init__()
        self.bot = bot
        self.army = army

    destination = discord.ui.TextInput(
        label="New Destination",
        placeholder="e.g., King's Landing or 456,789",
        required=True,
    )
    waypoints = discord.ui.TextInput(
        label="New Waypoints (Optional, semicolon-separated)",
        placeholder="e.g., The Twins; Moat Cailin",
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "🔄 Halting and re-issuing orders...", ephemeral=True
        )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, interaction.guild.id)
            user = await session.scalar(
                select(User).where(User.discord_id == interaction.user.id)
            )
            if not game or not user:
                return

            service = WarfareService(session)
            success, result, fog_msg = await service.redirect_army(
                game_id=game.game_id,
                user_id=user.user_id,
                army_id=self.army.army_id,
                new_dest_name=self.destination.value,
                new_waypoints=self.waypoints.value or None,
            )

            if not success:
                return await interaction.followup.send(result, ephemeral=True)

            # Schedule the new arrival task
            if new_army_id := result.get("army_id"):
                army_obj = await ArmyRepo.get_army_by_id(session, new_army_id)
                if army_obj and army_obj.arrival_time:
                    resolve_army_arrival.apply_async(
                        args=[new_army_id], eta=army_obj.arrival_time
                    )

            file = discord.File(result["image"], filename="route.png")
            embed = discord.Embed(
                title=f"🎺 {result['title']}", color=discord.Color.orange()
            )
            embed.add_field(name="Commander", value=result["commander"], inline=True)
            embed.add_field(name="Units", value=str(result["count"]), inline=True)
            embed.add_field(name="New ETA", value=result["time"], inline=False)
            embed.set_image(url="attachment://route.png")
            embed.set_footer(
                text=f"New Route: {result['origin']} -> {result['destination']}"
            )

            await interaction.followup.send(file=file, embed=embed, ephemeral=False)

            if fog_msg:
                gen_channel = discord.utils.get(
                    interaction.guild.text_channels, name="general-movements"
                )
                if gen_channel:
                    await gen_channel.send(f"🔄 **Redirect:** {fog_msg}")


# --- THE REDIRECT SELECTION VIEW ---
class RedirectSelectView(discord.ui.View):
    def __init__(self, bot: commands.Bot, armies: list):
        super().__init__(timeout=180)
        self.bot = bot

        options = []
        for army in armies[:25]:
            unit_type = "ships" if army.army_type == "SEA" else "men"
            status_icon = "⛵" if army.army_type == "SEA" else "🦶"
            options.append(
                discord.SelectOption(
                    label=f"{army.commander_name} ({army.troop_count} {unit_type})",
                    description=f"ID: {army.army_id} | Status: {army.status}",
                    value=str(army.army_id),
                    emoji=status_icon,
                )
            )

        self.select_menu = discord.ui.Select(
            placeholder="Select an army or fleet to redirect...", options=options
        )
        self.select_menu.callback = self.on_select
        self.add_item(self.select_menu)

    async def on_select(self, interaction: discord.Interaction):
        selected_army_id = int(self.select_menu.values[0])
        async with get_session() as session:
            army_obj = await ArmyRepo.get_army_by_id(session, selected_army_id)
            if not army_obj:
                return await interaction.response.send_message(
                    "Error: Unit not found.", ephemeral=True
                )

            modal = RedirectModal(bot=self.bot, army=army_obj)
            await interaction.response.send_modal(modal)

        self.stop()
        await interaction.message.edit(view=None)
