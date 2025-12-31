import discord
from discord.ext import commands
from sqlalchemy import select

# App Imports
from app.db.db_manager import get_session
from app.db.repositories import GameRepo, ArmyRepo
from app.services.warfare_service import WarfareService
from app.db.models import User, Army

# ============================================================
#                 MODAL 2 — CARGO INPUT
# ============================================================
class SailCargoModal(discord.ui.Modal, title="Embark Troops (Cargo Options)"):
    def __init__(self, bot: commands.Bot, fleet: Army, setup_data: dict):
        super().__init__()
        self.bot = bot
        self.fleet = fleet
        self.setup_data = setup_data  # ships, gold, dest, waypoints

        self.cargo = discord.ui.TextInput(
            label="Cargo (Men to Embark)",
            placeholder="1000, or inf:500 cav:200",
            required=True,
        )

        self.commander = discord.ui.TextInput(
            label="Cargo Commander Name (Optional)",
            placeholder="Leave blank for default name",
            required=False,
        )

        self.add_item(self.cargo)
        self.add_item(self.commander)

    async def on_submit(self, interaction: discord.Interaction):
        # Merge modal1 + modal2 data
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
            embed.add_field(name="Fleet Commander", value=result["commander"], inline=True)
            embed.add_field(name="Total Troops", value=str(result["count"]), inline=True)

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
#             INTERMEDIATE VIEW (THE FIX)
# ============================================================
class SailContinueView(discord.ui.View):
    def __init__(self, bot, fleet, setup_data):
        super().__init__(timeout=60)
        self.bot = bot
        self.fleet = fleet
        self.setup_data = setup_data

    @discord.ui.button(label="Load Cargo & Launch", style=discord.ButtonStyle.primary, emoji="📦")
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # This interaction IS allowed to open a Modal because it comes from a Button click
        await interaction.response.send_modal(
            SailCargoModal(bot=self.bot, fleet=self.fleet, setup_data=self.setup_data)
        )
        self.stop() # Stop listening to the view
        # Optional: Disable the button so they can't click it twice
        button.disabled = True
        await interaction.edit_original_response(content="✅ Cargo configuration opened.", view=self)

# ============================================================
#                 MODAL 1 — SETUP INPUTS
# ============================================================
class SailSetupModal(discord.ui.Modal, title="Issue Sailing Orders"):
    def __init__(self, bot: commands.Bot, fleet: Army, ship_capacity: int):
        super().__init__()
        self.bot = bot
        self.fleet = fleet
        self.has_cargo = bool(fleet.cargo and fleet.cargo.get("troop_count", 0) > 0)

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

        self.add_item(self.ships)
        self.add_item(self.gold)
        self.add_item(self.destination)
        self.add_item(self.waypoints)

    async def on_submit(self, interaction: discord.Interaction):
        # Extract modal values
        ships_input = self.ships.value
        dest = self.destination.value
        waypoints = self.waypoints.value or None

        try:
            gold_amount = int(self.gold.value or 0)
            if gold_amount < 0:
                raise ValueError()
        except ValueError:
            return await interaction.response.send_message(
                "❌ Invalid gold value. Must be a positive number.",
                ephemeral=True,
            )

        # If fleet already has cargo → WE CAN EXECUTE DIRECTLY
        if self.has_cargo:
            await interaction.response.send_message(
                "🌊 Orders received! Charting course...", ephemeral=True
            )
            # ... (Existing Logic for direct execution) ...
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
                    dest_name=dest,
                    units_input=None, 
                    commander=None,
                    gold_to_carry=gold_amount,
                    waypoints=waypoints,
                )
                if not success:
                    return await interaction.followup.send(result, ephemeral=True)

                file = discord.File(result["image"], filename="route.png")
                embed = discord.Embed(title="⛵ Sail Order Issued", color=discord.Color.blue())
                embed.description = result.get("journey_summary")
                embed.add_field(name="Fleet Commander", value=result["commander"], inline=True)
                embed.add_field(name="Total Troops", value=str(result["count"]), inline=True)
                if result.get("gold_carried", 0) > 0:
                    embed.add_field(name="Gold Carried", value=f"💰 {result['gold_carried']}", inline=True)
                embed.add_field(name="Final ETA", value=result["time"], inline=False)
                embed.set_image(url="attachment://route.png")
                embed.set_footer(text=f"Origin: {result['origin']} → Final Dest: {result['destination']}")
                
                await interaction.followup.send(file=file, embed=embed, ephemeral=False)

                if fog_msg:
                    gen_channel = discord.utils.get(interaction.guild.text_channels, name="general-movements")
                    if gen_channel:
                        await gen_channel.send(fog_msg)
            return

        # --- THIS IS THE FIX ---
        # Instead of send_modal, we send a message with a View (Button)
        modal2_data = {
            "ships": ships_input,
            "gold": gold_amount,
            "destination": dest,
            "waypoints": waypoints,
        }

        view = SailContinueView(self.bot, self.fleet, modal2_data)
        await interaction.response.send_message(
            "⚓ **Logistics Step:** Fleet configuration set. Click below to load cargo.",
            view=view,
            ephemeral=True
        )

# ============================================================
#              THE SELECT VIEW (unchanged)
# ============================================================
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
                return await interaction.response.send_message("Error: Fleet not found.", ephemeral=True)

            game = await GameRepo.get_active_game(session, interaction.guild.id)
            ship_capacity = game.ship_capacity if game else 100 

            modal = SailSetupModal(bot=self.bot, fleet=fleet_obj, ship_capacity=ship_capacity)
            await interaction.response.send_modal(modal)

        self.stop()
        await interaction.message.delete()