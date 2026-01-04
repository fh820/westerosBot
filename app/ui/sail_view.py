# import discord
# from discord.ext import commands
# from sqlalchemy import select

# # App Imports
# from app.db.db_manager import get_session
# from app.db.repositories import GameRepo, ArmyRepo
# from app.services.warfare_service import WarfareService
# from app.db.models import User, Army, Fief


# # ============================================================
# #                 MODAL 2 — CARGO INPUT
# # ============================================================
# class SailCargoModal(discord.ui.Modal, title="Provision Crew & Cargo"):
#     def __init__(self, bot: commands.Bot, fleet: Army, setup_data: dict):
#         super().__init__()
#         self.bot = bot
#         self.fleet = fleet
#         self.setup_data = setup_data  # ships, gold, dest, waypoints

#         self.cargo = discord.ui.TextInput(
#             label="Crew/Troops to Load",
#             placeholder="1000, or inf:500 cav:200",
#             required=True,
#         )

#         self.commander = discord.ui.TextInput(
#             label="Commander Name (Optional)",
#             placeholder="Leave blank for default name",
#             required=False,
#         )

#         self.add_item(self.cargo)
#         self.add_item(self.commander)

#     async def on_submit(self, interaction: discord.Interaction):
#         # Merge modal1 + modal2 data
#         units_to_sail = self.cargo.value
#         commander_name = self.commander.value or None
#         ships_input = self.setup_data["ships"]
#         gold_input = self.setup_data["gold"]
#         destination = self.setup_data["destination"]
#         waypoints = self.setup_data["waypoints"]

#         await interaction.response.send_message(
#             "🌊 Orders received! Charting course...", ephemeral=True
#         )

#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, interaction.guild.id)
#             user = await session.scalar(
#                 select(User).where(User.discord_id == interaction.user.id)
#             )

#             service = WarfareService(session)

#             success, result, fog_msg = await service.sail_fleet(
#                 game_id=game.game_id,
#                 user_id=user.user_id,
#                 fleet_id=self.fleet.army_id,
#                 ships_input=ships_input,
#                 dest_name=destination,
#                 units_input=units_to_sail,
#                 commander=commander_name,
#                 gold_to_carry=gold_input,
#                 waypoints=waypoints,
#             )

#             if not success:
#                 return await interaction.followup.send(result, ephemeral=True)

#             file = discord.File(result["image"], filename="route.png")
#             embed = discord.Embed(
#                 title="⛵ Sail Order Issued", color=discord.Color.blue()
#             )
#             embed.description = result.get("journey_summary")
#             embed.add_field(
#                 name="Fleet Commander", value=result["commander"], inline=True
#             )
#             embed.add_field(
#                 name="Total Troops", value=str(result["count"]), inline=True
#             )

#             if result.get("gold_carried", 0) > 0:
#                 embed.add_field(
#                     name="Gold Carried",
#                     value=f"💰 {result['gold_carried']}",
#                     inline=True,
#                 )

#             embed.add_field(name="Final ETA", value=result["time"], inline=False)
#             embed.set_image(url="attachment://route.png")
#             embed.set_footer(
#                 text=f"Origin: {result['origin']} → Final Dest: {result['destination']}"
#             )

#             await interaction.followup.send(file=file, embed=embed, ephemeral=False)

#             if fog_msg:
#                 gen_channel = discord.utils.get(
#                     interaction.guild.text_channels, name="general-movements"
#                 )
#                 if gen_channel:
#                     await gen_channel.send(fog_msg)


# # ============================================================
# #             INTERMEDIATE VIEW (BUTTON)
# # ============================================================
# class SailContinueView(discord.ui.View):
#     def __init__(self, bot, fleet, setup_data):
#         super().__init__(timeout=60)
#         self.bot = bot
#         self.fleet = fleet
#         self.setup_data = setup_data

#     @discord.ui.button(
#         label="Load Crew & Launch", style=discord.ButtonStyle.primary, emoji="📦"
#     )
#     async def continue_button(
#         self, interaction: discord.Interaction, button: discord.ui.Button
#     ):
#         # This interaction IS allowed to open a Modal because it comes from a Button click
#         await interaction.response.send_modal(
#             SailCargoModal(bot=self.bot, fleet=self.fleet, setup_data=self.setup_data)
#         )
#         self.stop()
#         # Optional: Disable the button so they can't click it twice
#         button.disabled = True
#         await interaction.edit_original_response(
#             content="✅ Provisioning crew...", view=self
#         )


# # ============================================================
# #                 MODAL 1 — SETUP INPUTS
# # ============================================================
# class SailSetupModal(discord.ui.Modal, title="Issue Sailing Orders"):
#     def __init__(self, bot: commands.Bot, fleet: Army, ship_capacity: int):
#         super().__init__()
#         self.bot = bot
#         self.fleet = fleet
#         self.has_cargo = bool(fleet.cargo and fleet.cargo.get("troop_count", 0) > 0)

#         self.ships = discord.ui.TextInput(
#             label=f"Ships to Send (Capacity: {ship_capacity} men/ship)",
#             placeholder=f"all, or a number up to {fleet.troop_count}",
#             default="all",
#             required=True,
#         )

#         self.gold = discord.ui.TextInput(
#             label="Gold to Carry (Optional)",
#             placeholder="e.g., 500",
#             default="0",
#             required=False,
#         )

#         self.destination = discord.ui.TextInput(
#             label="Destination",
#             placeholder="Type a port, fief, or coordinates",
#             required=True,
#         )

#         self.waypoints = discord.ui.TextInput(
#             label="Waypoints (Optional, semicolon-separated)",
#             placeholder="e.g., Dragonstone; 555,888",
#             required=False,
#         )

#         self.add_item(self.ships)
#         self.add_item(self.gold)
#         self.add_item(self.destination)
#         self.add_item(self.waypoints)

#     async def on_submit(self, interaction: discord.Interaction):
#         # Extract modal values
#         ships_input = self.ships.value
#         dest = self.destination.value
#         waypoints = self.waypoints.value or None

#         try:
#             gold_amount = int(self.gold.value or 0)
#             if gold_amount < 0:
#                 raise ValueError()
#         except ValueError:
#             return await interaction.response.send_message(
#                 "❌ Invalid gold value. Must be a positive number.",
#                 ephemeral=True,
#             )

#         # 1. IF FLEET HAS CARGO -> EXECUTE IMMEDIATELY
#         if self.has_cargo:
#             await interaction.response.send_message(
#                 "🌊 Orders received! Charting course...", ephemeral=True
#             )
#             async with get_session() as session:
#                 game = await GameRepo.get_active_game(session, interaction.guild.id)
#                 user = await session.scalar(
#                     select(User).where(User.discord_id == interaction.user.id)
#                 )
#                 service = WarfareService(session)
#                 success, result, fog_msg = await service.sail_fleet(
#                     game_id=game.game_id,
#                     user_id=user.user_id,
#                     fleet_id=self.fleet.army_id,
#                     ships_input=ships_input,
#                     dest_name=dest,
#                     units_input=None,  # None implies "Use Existing Cargo" inside service
#                     commander=None,
#                     gold_to_carry=gold_amount,
#                     waypoints=waypoints,
#                 )
#                 if not success:
#                     return await interaction.followup.send(result, ephemeral=True)

#                 file = discord.File(result["image"], filename="route.png")
#                 embed = discord.Embed(
#                     title="⛵ Sail Order Issued", color=discord.Color.blue()
#                 )
#                 embed.description = result.get("journey_summary")
#                 embed.add_field(
#                     name="Fleet Commander", value=result["commander"], inline=True
#                 )
#                 embed.add_field(
#                     name="Total Troops", value=str(result["count"]), inline=True
#                 )
#                 if result.get("gold_carried", 0) > 0:
#                     embed.add_field(
#                         name="Gold Carried",
#                         value=f"💰 {result['gold_carried']}",
#                         inline=True,
#                     )
#                 embed.add_field(name="Final ETA", value=result["time"], inline=False)
#                 embed.set_image(url="attachment://route.png")
#                 embed.set_footer(
#                     text=f"Origin: {result['origin']} → Final Dest: {result['destination']}"
#                 )

#                 await interaction.followup.send(file=file, embed=embed, ephemeral=False)

#                 if fog_msg:
#                     gen_channel = discord.utils.get(
#                         interaction.guild.text_channels, name="general-movements"
#                     )
#                     if gen_channel:
#                         await gen_channel.send(fog_msg)
#             return

#         # 2. IF FLEET IS EMPTY -> CHECK FIEF LOCATION
#         async with get_session() as session:
#             stmt_check = select(Fief).where(
#                 Fief.game_id == self.fleet.game_id,
#                 Fief.location_x == self.fleet.location_x,
#                 Fief.location_y == self.fleet.location_y,
#                 Fief.owner_id == self.fleet.house_id,
#             )
#             is_at_friendly_fief = (
#                 await session.execute(stmt_check)
#             ).scalar_one_or_none()

#             if is_at_friendly_fief:
#                 # Can load cargo -> Show Button
#                 modal2_data = {
#                     "ships": ships_input,
#                     "gold": gold_amount,
#                     "destination": dest,
#                     "waypoints": waypoints,
#                 }
#                 view = SailContinueView(self.bot, self.fleet, modal2_data)
#                 await interaction.response.send_message(
#                     "⚓ **Logistics:** You are at a friendly port. Click below to provision crew/cargo.",
#                     view=view,
#                     ephemeral=True,
#                 )
#             else:
#                 # Cannot load cargo -> Block Action
#                 await interaction.response.send_message(
#                     f"⛔ **Cannot Sail:** This fleet is empty (0 Crew) and you are not at a friendly Fief.\n"
#                     f"You cannot provision fresh crew at coordinates `{self.fleet.location_x}, {self.fleet.location_y}`.\n\n"
#                     f"👉 **Solution:** March a land army to this location and use `!embark` to man the ships.",
#                     ephemeral=True,
#                 )


# # ============================================================
# #              THE SELECT VIEW (unchanged)
# # ============================================================
# class FleetSelectView(discord.ui.View):
#     def __init__(self, bot: commands.Bot, fleets: list):
#         super().__init__(timeout=180)
#         self.bot = bot
#         options = [
#             discord.SelectOption(
#                 label=f"{fleet.commander_name} ({fleet.troop_count} ships)",
#                 description=f"ID: {fleet.army_id} | Status: {fleet.status}",
#                 value=str(fleet.army_id),
#             )
#             for fleet in fleets[:25]
#         ]
#         self.select_menu = discord.ui.Select(
#             placeholder="Select a fleet to command...", options=options
#         )
#         self.select_menu.callback = self.on_select
#         self.add_item(self.select_menu)

#     async def on_select(self, interaction: discord.Interaction):
#         fleet_id = int(self.select_menu.values[0])
#         async with get_session() as session:
#             fleet_obj = await ArmyRepo.get_army_by_id(session, fleet_id)
#             if not fleet_obj:
#                 return await interaction.response.send_message(
#                     "Error: Fleet not found.", ephemeral=True
#                 )

#             game = await GameRepo.get_active_game(session, interaction.guild.id)
#             ship_capacity = game.ship_capacity if game else 100

#             modal = SailSetupModal(
#                 bot=self.bot, fleet=fleet_obj, ship_capacity=ship_capacity
#             )
#             await interaction.response.send_modal(modal)

#         self.stop()
#         await interaction.message.delete()


import discord
from discord.ext import commands
from sqlalchemy import select

# App Imports
from app.db.db_manager import get_session
from app.db.repositories import GameRepo, ArmyRepo
from app.services.warfare_service import WarfareService
from app.db.models import User, Army, Fief


# ============================================================
#                 MODAL 2 — CARGO INPUT
# ============================================================
# No changes needed in this modal
class SailCargoModal(discord.ui.Modal, title="Provision Crew & Cargo"):
    def __init__(self, bot: commands.Bot, fleet: Army, setup_data: dict):
        super().__init__()
        self.bot = bot
        self.fleet = fleet
        self.setup_data = setup_data  # ships, gold, dest, waypoints

        self.cargo = discord.ui.TextInput(
            label="Crew/Troops to Load",
            placeholder="1000, or inf:500 cav:200",
            required=True,
        )
        self.commander = discord.ui.TextInput(
            label="Commander Name (Optional)",
            placeholder="Leave blank for default name",
            required=False,
        )
        self.add_item(self.cargo)
        self.add_item(self.commander)

    async def on_submit(self, interaction: discord.Interaction):
        # This logic is correct and requires no changes.
        units_to_sail = self.cargo.value
        commander_name = self.commander.value or None
        ships_input = self.setup_data["ships"]
        gold_input = self.setup_data["gold"]
        destination = self.setup_data["destination"]
        waypoints = self.setup_data["waypoints"]

        await interaction.response.send_message(
            "🌊 Orders received! Charting course...", ephemeral=True
        )
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, interaction.guild.id)
            user = await session.scalar(
                select(User).where(User.discord_id == interaction.user.id)
            )
            service = WarfareService(session)
            success, result, fog_msg = await service.sail_fleet(
                game_id=game.game_id,
                user_id=user.user_id,
                fleet_id=self.fleet.army_id,
                ships_input=ships_input,
                dest_name=destination,
                units_input=units_to_sail,
                commander=commander_name,
                gold_to_carry=gold_input,
                waypoints=waypoints,
            )
            if not success:
                return await interaction.followup.send(result, ephemeral=True)

            file = discord.File(result["image"], filename="route.png")
            embed = discord.Embed(
                title="⛵ Sail Order Issued", color=discord.Color.blue()
            )
            embed.description = result.get("journey_summary")
            embed.add_field(
                name="Fleet Commander", value=result["commander"], inline=True
            )
            embed.add_field(
                name="Total Troops", value=str(result["count"]), inline=True
            )
            if result.get("gold_carried", 0) > 0:
                embed.add_field(
                    name="Gold Carried",
                    value=f"💰 {result['gold_carried']}",
                    inline=True,
                )
            embed.add_field(name="Final ETA", value=result["time"], inline=False)
            embed.set_image(url="attachment://route.png")
            embed.set_footer(
                text=f"Origin: {result['origin']} → Final Dest: {result['destination']}"
            )
            await interaction.followup.send(file=file, embed=embed, ephemeral=False)
            if fog_msg:
                gen_channel = discord.utils.get(
                    interaction.guild.text_channels, name="general-movements"
                )
                if gen_channel:
                    await gen_channel.send(fog_msg)


# ============================================================
#             INTERMEDIATE VIEW (BUTTON)
# ============================================================
# No changes needed in this view
class SailContinueView(discord.ui.View):
    def __init__(self, bot, fleet, setup_data):
        super().__init__(timeout=60)
        self.bot = bot
        self.fleet = fleet
        self.setup_data = setup_data

    @discord.ui.button(
        label="Load Crew & Launch", style=discord.ButtonStyle.primary, emoji="📦"
    )
    async def continue_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(
            SailCargoModal(bot=self.bot, fleet=self.fleet, setup_data=self.setup_data)
        )
        self.stop()
        button.disabled = True
        await interaction.edit_original_response(
            content="✅ Provisioning crew...", view=self
        )


# ============================================================
#                 MODAL 1 — SETUP INPUTS
# ============================================================
class SailSetupModal(discord.ui.Modal, title="Issue Sailing Orders"):
    def __init__(self, bot: commands.Bot, fleet: Army, ship_capacity: int):
        super().__init__()
        self.bot = bot
        self.fleet = fleet
        self.has_cargo = bool(fleet.cargo and fleet.cargo.get("troop_count", 0) > 0)

        # --- FIX: DEFINE UI ELEMENTS FIRST ---
        self.ships = discord.ui.TextInput(
            label=f"Ships to Send (Capacity: {ship_capacity} men/ship)",
            placeholder=f"all, or a number up to {fleet.troop_count}",
            default="all",
            required=True,
        )
        self.gold = discord.ui.TextInput(
            label="Gold to Carry (Optional)",
            placeholder="e.g., 500",
            default="0",
            required=False,
        )
        self.destination = discord.ui.TextInput(
            label="Destination",
            placeholder="Type a port, fief, or coordinates",
            required=True,
        )
        self.waypoints = discord.ui.TextInput(
            label="Waypoints (Optional, semicolon-separated)",
            placeholder="e.g., Dragonstone; 555,888",
            required=False,
        )
        # --- END FIX ---

        # Now we can safely access self.gold
        self.is_gold_locked = False
        if fleet.treasury and fleet.treasury > 0:
            self.gold.placeholder = f"Fleet is already carrying {fleet.treasury} gold."
            self.gold.default = str(fleet.treasury)
            self.gold.disabled = True
            self.is_gold_locked = True

        # Add items to the modal AFTER they have been fully configured
        self.add_item(self.ships)
        self.add_item(self.gold)
        self.add_item(self.destination)
        self.add_item(self.waypoints)

    async def on_submit(self, interaction: discord.Interaction):
        ships_input = self.ships.value
        dest = self.destination.value
        waypoints = self.waypoints.value or None

        # --- FIX 2: Check the state flag before processing any new gold ---
        gold_amount = 0
        if not self.is_gold_locked:
            try:
                gold_amount = int(self.gold.value or 0)
                if gold_amount < 0:
                    raise ValueError()
            except ValueError:
                return await interaction.response.send_message(
                    "❌ Invalid gold value. Must be a positive number.",
                    ephemeral=True,
                )

        # 1. IF FLEET HAS CARGO -> EXECUTE IMMEDIATELY
        if self.has_cargo:
            await interaction.response.send_message(
                "🌊 Orders received! Charting course...", ephemeral=True
            )
            async with get_session() as session:
                game = await GameRepo.get_active_game(session, interaction.guild.id)
                user = await session.scalar(
                    select(User).where(User.discord_id == interaction.user.id)
                )
                service = WarfareService(session)
                # 'gold_amount' will be 0 here if the field was locked, which is correct.
                success, result, fog_msg = await service.sail_fleet(
                    game_id=game.game_id,
                    user_id=user.user_id,
                    fleet_id=self.fleet.army_id,
                    ships_input=ships_input,
                    dest_name=dest,
                    units_input=None,
                    commander=None,
                    gold_to_carry=gold_amount,
                    waypoints=waypoints,
                )
                # ... (rest of the execution logic is unchanged) ...
                if not success:
                    return await interaction.followup.send(result, ephemeral=True)
                file = discord.File(result["image"], filename="route.png")
                embed = discord.Embed(
                    title="⛵ Sail Order Issued", color=discord.Color.blue()
                )
                embed.description = result.get("journey_summary")
                embed.add_field(
                    name="Fleet Commander", value=result["commander"], inline=True
                )
                embed.add_field(
                    name="Total Troops", value=str(result["count"]), inline=True
                )
                if result.get("gold_carried", 0) > 0:
                    embed.add_field(
                        name="Gold Carried",
                        value=f"💰 {result['gold_carried']}",
                        inline=True,
                    )
                embed.add_field(name="Final ETA", value=result["time"], inline=False)
                embed.set_image(url="attachment://route.png")
                embed.set_footer(
                    text=f"Origin: {result['origin']} → Final Dest: {result['destination']}"
                )
                await interaction.followup.send(file=file, embed=embed, ephemeral=False)
                if fog_msg:
                    gen_channel = discord.utils.get(
                        interaction.guild.text_channels, name="general-movements"
                    )
                    if gen_channel:
                        await gen_channel.send(fog_msg)
            return

        # 2. IF FLEET IS EMPTY -> CHECK FIEF LOCATION
        async with get_session() as session:
            stmt_check = select(Fief).where(
                Fief.game_id == self.fleet.game_id,
                Fief.location_x == self.fleet.location_x,
                Fief.location_y == self.fleet.location_y,
                Fief.owner_id == self.fleet.house_id,
            )
            is_at_friendly_fief = (
                await session.execute(stmt_check)
            ).scalar_one_or_none()

            if is_at_friendly_fief:
                # 'gold_amount' is correctly passed to the next modal's data.
                modal2_data = {
                    "ships": ships_input,
                    "gold": gold_amount,
                    "destination": dest,
                    "waypoints": waypoints,
                }
                view = SailContinueView(self.bot, self.fleet, modal2_data)
                await interaction.response.send_message(
                    "⚓ **Logistics:** You are at a friendly port. Click below to provision crew/cargo.",
                    view=view,
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"⛔ **Cannot Sail:** This fleet is empty (0 Crew) and you are not at a friendly Fief.\n"
                    f"You cannot provision fresh crew at coordinates `{self.fleet.location_x}, {self.fleet.location_y}`.\n\n"
                    f"👉 **Solution:** March a land army to this location and use `!embark` to man the ships.",
                    ephemeral=True,
                )


# ============================================================
#              THE SELECT VIEW (unchanged)
# ============================================================
# No changes needed in this view as it already passes the full fleet object.
class FleetSelectView(discord.ui.View):
    def __init__(self, bot: commands.Bot, fleets: list):
        super().__init__(timeout=180)
        self.bot = bot
        options = [
            discord.SelectOption(
                label=f"{fleet.commander_name} ({fleet.troop_count} ships)",
                description=f"ID: {fleet.army_id} | Status: {fleet.status}",
                value=str(fleet.army_id),
            )
            for fleet in fleets[:25]
        ]
        self.select_menu = discord.ui.Select(
            placeholder="Select a fleet to command...", options=options
        )
        self.select_menu.callback = self.on_select
        self.add_item(self.select_menu)

    async def on_select(self, interaction: discord.Interaction):
        fleet_id = int(self.select_menu.values[0])
        async with get_session() as session:
            fleet_obj = await ArmyRepo.get_army_by_id(session, fleet_id)
            if not fleet_obj:
                return await interaction.response.send_message(
                    "Error: Fleet not found.", ephemeral=True
                )
            game = await GameRepo.get_active_game(session, interaction.guild.id)
            ship_capacity = game.ship_capacity if game else 100
            modal = SailSetupModal(
                bot=self.bot, fleet=fleet_obj, ship_capacity=ship_capacity
            )
            await interaction.response.send_modal(modal)
        self.stop()
        await interaction.message.delete()


class DirectSailView(discord.ui.View):
    """
    A specific view for when a user types !sail [ID].
    Provides a button to immediately open the modal for that fleet.
    """

    def __init__(self, bot, fleet, ship_capacity):
        super().__init__(timeout=60)
        self.bot = bot
        self.fleet = fleet
        self.ship_capacity = ship_capacity

    @discord.ui.button(
        label="Configure Sail Orders", style=discord.ButtonStyle.primary, emoji="⚓"
    )
    async def configure(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Verify ownership again just in case
        if (
            interaction.user.id != self.fleet.player_discord_id
            and not interaction.user.guild_permissions.administrator
        ):
            return await interaction.response.send_message(
                "❌ You do not control this fleet.", ephemeral=True
            )

        modal = SailSetupModal(
            bot=self.bot, fleet=self.fleet, ship_capacity=self.ship_capacity
        )
        await interaction.response.send_modal(modal)
        self.stop()
        # Disable button after clicking to prevent spam
        button.disabled = True
        await interaction.edit_original_response(
            content=f"📝 Configuring **{self.fleet.commander_name}**...", view=self
        )
