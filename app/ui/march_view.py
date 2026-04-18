# # In app/ui/march_view.py

# import discord
# from discord.ext import commands
# from sqlalchemy import select

# from app.db.db_manager import get_session
# from app.db.repositories import GameRepo, ArmyRepo
# from app.services.warfare_service import WarfareService
# from app.db.models import User, Army
# from app.tasks.light_tasks import resolve_army_arrival


# # --- THE CORRECTED ALL-IN-ONE MODAL ---
# class MarchModal(discord.ui.Modal, title="Issue March Orders"):
#     def __init__(self, bot: commands.Bot, army_id: int):
#         super().__init__()
#         self.bot = bot
#         self.army_id = army_id

#     # --- CORE FIX: DEFINE FIELDS IN THE CORRECT VISUAL ORDER ---
#     # The order of these class variable definitions determines the order in the modal.

#     destination = discord.ui.TextInput(
#         label="Destination",
#         placeholder="Type the exact name of a fief or coordinates (e.g., Winterfell)",
#         required=True,
#     )

#     units = discord.ui.TextInput(
#         label="Units to Move",
#         placeholder="all, 1000, or inf:500 cav:200",
#         required=True,
#     )

#     commander = discord.ui.TextInput(
#         label="Commander Name (Optional)",
#         placeholder="Leave blank to use the current commander.",
#         required=False,
#     )

#     gold = discord.ui.TextInput(
#         label="Gold to Carry (Optional)",
#         placeholder="e.g., 500",
#         default="0",
#         required=False,
#     )

#     waypoints = discord.ui.TextInput(
#         label="Waypoints (Optional, semicolon-separated)",
#         placeholder="e.g., Moat Cailin; 466,943; The Twins",
#         required=False,
#     )
#     # --- END OF FIX ---

#     async def on_submit(self, interaction: discord.Interaction):
#         await interaction.response.send_message(
#             "🗺️ Orders received! Calculating route...", ephemeral=True
#         )
#         try:
#             gold_amount = int(self.gold.value or 0)
#             if gold_amount < 0:
#                 raise ValueError
#         except ValueError:
#             return await interaction.followup.send(
#                 "❌ Invalid gold amount. Must be a positive number.", ephemeral=True
#             )

#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, interaction.guild.id)
#             user = await session.scalar(
#                 select(User).where(User.discord_id == interaction.user.id)
#             )
#             if not game or not user:
#                 return

#             service = WarfareService(session)
#             success, result, fog_msg = await service.march_army(
#                 game_id=game.game_id,
#                 user_id=user.user_id,
#                 identifier=str(self.army_id),
#                 dest_name=self.destination.value,
#                 units_input=self.units.value,
#                 commander=self.commander.value or None,
#                 gold_to_carry=gold_amount,  # This is correctly passed
#                 waypoints=self.waypoints.value or None,
#             )

#             if not success:
#                 return await interaction.followup.send(result, ephemeral=True)

#             # Celery task scheduling should be handled by the service, but if you do it here,
#             # it's important to use the task ID that the service should be setting on the object.
#             # Your current code for this part is fine.
#             if "alert_message" in result:
#                 await interaction.followup.send(
#                     content=result["alert_message"], ephemeral=True
#                 )

#             file = discord.File(result["image"], filename="route.png")
#             embed = discord.Embed(
#                 title="🎺 March Order Issued", color=discord.Color.red()
#             )
#             embed.add_field(name="Commander", value=result["commander"], inline=True)
#             embed.add_field(name="Troops", value=str(result["count"]), inline=True)
#             if result.get("gold_carried", 0) > 0:
#                 embed.add_field(
#                     name="Gold Carried",
#                     value=f"💰 {result['gold_carried']}",
#                     inline=True,
#                 )
#             embed.add_field(name="ETA", value=result["time"], inline=False)
#             embed.set_image(url="attachment://route.png")
#             embed.set_footer(
#                 text=f"Origin: {result['origin']} -> Dest: {result['destination']}"
#             )

#             await interaction.followup.send(file=file, embed=embed, ephemeral=False)

#             if fog_msg:
#                 gen_channel = discord.utils.get(
#                     interaction.guild.text_channels, name="general-movements"
#                 )
#                 if gen_channel:
#                     await gen_channel.send(fog_msg)


# class ArmySelectView(discord.ui.View):
#     def __init__(self, bot: commands.Bot, armies: list):
#         super().__init__(timeout=180)
#         self.bot = bot

#         options = [
#             discord.SelectOption(
#                 label=f"{army.commander_name} ({army.troop_count} men)",
#                 description=f"ID: {army.army_id} | Status: {army.status}",
#                 value=str(army.army_id),
#             )
#             for army in armies[:25]
#         ]

#         # Create the Select component and link its callback
#         self.select_menu = discord.ui.Select(
#             placeholder="Select an army to command...", options=options
#         )
#         self.select_menu.callback = self.on_select
#         self.add_item(self.select_menu)

#     async def on_select(self, interaction: discord.Interaction):
#         """Callback for when the user selects an army."""
#         # Get the chosen army ID from the dropdown's value
#         selected_army_id = int(self.select_menu.values[0])

#         # Launch the new, all-in-one modal
#         modal = MarchModal(bot=self.bot, army_id=selected_army_id)
#         await interaction.response.send_modal(modal)

#         # Optional: Disable the view after use to prevent re-submission
#         self.stop()
#         await interaction.message.edit(view=None)


# class JourneyModal(discord.ui.Modal, title="Plan a Journey"):
#     def __init__(self, bot: commands.Bot, army: Army):
#         super().__init__()
#         self.bot = bot
#         self.army = army

#     destination = discord.ui.TextInput(
#         label="Destination",
#         placeholder="e.g., Winterfell or 1234,5678",
#         required=True,
#     )
#     # NEW: Let the user choose the travel mode for planning
#     travel_mode = discord.ui.TextInput(
#         label="Travel Mode (optimal, land_only, sea_only)",
#         placeholder="Default is 'optimal'",
#         default="optimal",
#         required=False,
#     )
#     units = discord.ui.TextInput(
#         label="Units to Simulate (Optional)",
#         placeholder="e.g., all, 1000, or inf:500",
#         default="all",
#         required=False,
#     )
#     waypoints = discord.ui.TextInput(
#         label="Waypoints (Optional, semicolon-separated)",
#         placeholder="e.g., Moat Cailin; The Twins",
#         required=False,
#     )

#     async def on_submit(self, interaction: discord.Interaction):
#         await interaction.response.send_message(
#             "🗺️ Calculating route...", ephemeral=True
#         )

#         # Sanitize travel mode input
#         mode = self.travel_mode.value.strip().lower()
#         if mode not in ["optimal", "land_only", "sea_only"]:
#             mode = "optimal"  # Default to optimal if input is invalid

#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, interaction.guild.id)
#             if not game:
#                 return

#             service = WarfareService(session)
#             # We now call the generic 'plan_journey' which is mode-agnostic
#             success, result = await service.plan_journey(
#                 game_id=game.game_id,
#                 source_army_id=self.army.army_id,
#                 dest_name=self.destination.value,
#                 units_input=self.units.value,
#                 travel_mode_req=mode,  # Pass the chosen mode
#                 waypoints=self.waypoints.value or None,
#             )

#             if not success:
#                 return await interaction.followup.send(result, ephemeral=True)

#             file = discord.File(result["image"], filename="journey.png")
#             embed = discord.Embed(title="🗺️ Journey Plan", color=discord.Color.blue())
#             embed.add_field(
#                 name="Simulated Troops", value=str(result["army_size"]), inline=True
#             )
#             embed.add_field(name="Travel Mode", value=result["mode"], inline=True)
#             embed.add_field(name="Estimated Time", value=result["time"], inline=False)
#             embed.set_image(url="attachment://journey.png")
#             embed.set_footer(
#                 text=f"Route: {result['origin']} -> {result['destination']}"
#             )

#             await interaction.followup.send(file=file, embed=embed, ephemeral=False)


# # --- THE NEW UNIVERSAL ARMY/FLEET SELECTION VIEW ---
# class JourneyArmySelectView(discord.ui.View):
#     def __init__(self, bot: commands.Bot, armies: list):
#         super().__init__(timeout=180)
#         self.bot = bot

#         options = []
#         for army in armies[:25]:
#             unit_type = "ships" if army.army_type == "SEA" else "men"
#             options.append(
#                 discord.SelectOption(
#                     label=f"{army.commander_name} ({army.troop_count} {unit_type})",
#                     description=f"ID: {army.army_id} | Type: {army.army_type}",
#                     value=str(army.army_id),
#                 )
#             )

#         self.select_menu = discord.ui.Select(
#             placeholder="Select a starting unit for your plan...", options=options
#         )
#         self.select_menu.callback = self.on_select
#         self.add_item(self.select_menu)

#     async def on_select(self, interaction: discord.Interaction):
#         selected_army_id = int(self.select_menu.values[0])
#         async with get_session() as session:
#             army_obj = await ArmyRepo.get_army_by_id(session, selected_army_id)
#             if not army_obj:
#                 return await interaction.response.send_message(
#                     "Error: Unit not found.", ephemeral=True
#                 )

#             modal = JourneyModal(bot=self.bot, army=army_obj)
#             await interaction.response.send_modal(modal)

#         self.stop()
#         await interaction.message.edit(view=None)


import discord
from discord.ext import commands
from sqlalchemy import select

from app.db.db_manager import get_session
from app.db.repositories import GameRepo, ArmyRepo
from app.services.warfare_service import WarfareService
from app.db.models import User, Army
from app.tasks.light_tasks import resolve_army_arrival


class MarchModal(discord.ui.Modal, title="Issue March Orders"):
    def __init__(self, bot: commands.Bot, army: Army):
        super().__init__()
        self.bot = bot
        self.army_id = army.army_id

        # --- FIX 1: Create a state flag to track if the gold input is locked ---
        self.is_gold_locked = False

        if army.treasury and army.treasury > 0:
            self.gold.placeholder = f"Army is already carrying {army.treasury} gold."
            self.gold.default = str(army.treasury)
            self.gold.disabled = True  # This correctly disables the UI element visually
            self.is_gold_locked = True  # We set our own flag to track this state

    destination = discord.ui.TextInput(
        label="Destination",
        placeholder="Type the exact name of a fief or coordinates (e.g., Winterfell)",
        required=True,
    )
    units = discord.ui.TextInput(
        label="Units to Move",
        placeholder="all, 1000, or inf:500 cav:200",
        required=True,
    )
    commander = discord.ui.TextInput(
        label="Commander Name (Optional)",
        placeholder="Leave blank to use the current commander.",
        required=False,
    )
    gold = discord.ui.TextInput(
        label="Gold to Carry (Optional)",
        placeholder="e.g., 500",
        default="0",
        required=False,
    )
    waypoints = discord.ui.TextInput(
        label="Waypoints (Optional, semicolon-separated)",
        placeholder="e.g., Moat Cailin; 466,943; The Twins",
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "🗺️ Orders received! Calculating route...", ephemeral=True
        )

        gold_amount = 0
        # --- FIX 2: Check our reliable state flag instead of the component property ---
        if not self.is_gold_locked:
            try:
                gold_amount = int(self.gold.value or 0)
                if gold_amount < 0:
                    raise ValueError
            except ValueError:
                return await interaction.followup.send(
                    "❌ Invalid gold amount. Must be a positive number.", ephemeral=True
                )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, interaction.guild.id)
            user = await session.scalar(
                select(User).where(User.discord_id == interaction.user.id)
            )
            if not game or not user:
                return

            service = WarfareService(session)
            success, result, fog_msg = await service.march_army(
                game_id=game.game_id,
                user_id=user.user_id,
                identifier=str(self.army_id),
                dest_name=self.destination.value,
                units_input=self.units.value,
                commander=self.commander.value or None,
                gold_to_carry=gold_amount,
                waypoints=self.waypoints.value or None,
            )

            if not success:
                return await interaction.followup.send(result, ephemeral=True)

            if "alert_message" in result:
                await interaction.followup.send(
                    content=result["alert_message"], ephemeral=True
                )

            file = discord.File(result["image"], filename="route.png")
            embed = discord.Embed(
                title="🎺 March Order Issued", color=discord.Color.red()
            )
            embed.add_field(name="Commander", value=result["commander"], inline=True)
            embed.add_field(name="Troops", value=str(result["count"]), inline=True)
            if result.get("gold_carried", 0) > 0:
                embed.add_field(
                    name="Gold Carried",
                    value=f"💰 {result['gold_carried']}",
                    inline=True,
                )
            embed.add_field(name="ETA", value=result["time"], inline=False)
            embed.set_image(url="attachment://route.png")
            embed.set_footer(
                text=f"Origin: {result['origin']} -> Dest: {result['destination']}"
            )

            await interaction.followup.send(file=file, embed=embed, ephemeral=False)

            if fog_msg:
                gen_channel = discord.utils.get(
                    interaction.guild.text_channels, name="general-movements"
                )
                if gen_channel:
                    await gen_channel.send(fog_msg)


# (The rest of the file is correct and does not need changes)
class ArmySelectView(discord.ui.View):
    def __init__(self, bot: commands.Bot, armies: list[Army]):
        super().__init__(timeout=180)
        self.bot = bot
        self.armies_map = {army.army_id: army for army in armies}

        options = [
            discord.SelectOption(
                label=f"{army.commander_name} ({army.troop_count} men)",
                description=f"ID: {army.army_id} | Status: {army.status}",
                value=str(army.army_id),
            )
            for army in armies[:25]
        ]

        self.select_menu = discord.ui.Select(
            placeholder="Select an army to command...", options=options
        )
        self.select_menu.callback = self.on_select
        self.add_item(self.select_menu)

    async def on_select(self, interaction: discord.Interaction):
        selected_army_id = int(self.select_menu.values[0])
        army_to_command = self.armies_map.get(selected_army_id)
        if not army_to_command:
            return await interaction.response.send_message(
                "Error: Army not found.", ephemeral=True
            )

        modal = MarchModal(bot=self.bot, army=army_to_command)
        await interaction.response.send_modal(modal)

        self.stop()
        await interaction.message.edit(view=None)


class JourneyModal(discord.ui.Modal, title="Plan a Journey"):
    def __init__(self, bot: commands.Bot, army: Army):
        super().__init__()
        self.bot = bot
        self.army = army

    destination = discord.ui.TextInput(
        label="Destination",
        placeholder="e.g., Winterfell or 1234,5678",
        required=True,
    )
    travel_mode = discord.ui.TextInput(
        label="Travel Mode (optimal, land_only, sea_only)",
        placeholder="Default is 'optimal'",
        default="optimal",
        required=False,
    )
    units = discord.ui.TextInput(
        label="Units to Simulate (Optional)",
        placeholder="e.g., all, 1000, or inf:500",
        default="all",
        required=False,
    )
    waypoints = discord.ui.TextInput(
        label="Waypoints (Optional, semicolon-separated)",
        placeholder="e.g., Moat Cailin; The Twins",
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "🗺️ Calculating route...", ephemeral=True
        )

        mode = self.travel_mode.value.strip().lower()
        if mode not in ["optimal", "land_only", "sea_only"]:
            mode = "optimal"

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, interaction.guild.id)
            if not game:
                return

            service = WarfareService(session)
            success, result = await service.plan_journey(
                game_id=game.game_id,
                source_army_id=self.army.army_id,
                dest_name=self.destination.value,
                units_input=self.units.value,
                travel_mode_req=mode,
                waypoints=self.waypoints.value or None,
            )

            if not success:
                return await interaction.followup.send(result, ephemeral=True)

            file = discord.File(result["image"], filename="journey.png")
            embed = discord.Embed(title="🗺️ Journey Plan", color=discord.Color.blue())
            embed.add_field(
                name="Simulated Troops", value=str(result["army_size"]), inline=True
            )
            embed.add_field(name="Travel Mode", value=result["mode"], inline=True)
            embed.add_field(name="Estimated Time", value=result["time"], inline=False)
            embed.set_image(url="attachment://journey.png")
            embed.set_footer(
                text=f"Route: {result['origin']} -> {result['destination']}"
            )

            await interaction.followup.send(file=file, embed=embed, ephemeral=False)


class JourneyArmySelectView(discord.ui.View):
    def __init__(self, bot: commands.Bot, armies: list):
        super().__init__(timeout=180)
        self.bot = bot
        print('sfd')

        options = []
        for army in armies[:25]:
            unit_type = "ships" if army.army_type == "SEA" else "men"
            options.append(
                discord.SelectOption(
                    label=f"{army.commander_name} ({army.troop_count} {unit_type})",
                    description=f"ID: {army.army_id} | Type: {army.army_type}",
                    value=str(army.army_id),
                )
            )

        self.select_menu = discord.ui.Select(
            placeholder="Select a starting unit for your plan...", options=options
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

            modal = JourneyModal(bot=self.bot, army=army_obj)
            await interaction.response.send_modal(modal)

        self.stop()
        await interaction.message.edit(view=None)


class DirectMarchView(discord.ui.View):
    """
    A specific view for when a user types !march [ID].
    Provides a button to immediately open the modal for that army.
    """

    def __init__(self, bot: commands.Bot, army: Army):
        super().__init__(timeout=60)
        self.bot = bot
        self.army = army

    @discord.ui.button(
        label="Issue March Orders", style=discord.ButtonStyle.success, emoji="👣"
    )
    async def configure(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Double check ownership (though command already checked it)
        # Note: We rely on the command's check, but a redundant check is safer if passing objects around.
        # For simplicity in this view, we assume the command validated the user.

        modal = MarchModal(bot=self.bot, army=self.army)
        await interaction.response.send_modal(modal)
        self.stop()
        button.disabled = True
        await interaction.edit_original_response(
            content=f"📝 issuing orders to **{self.army.commander_name}**...", view=self
        )
