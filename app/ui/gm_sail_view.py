import discord
from discord.ui import Modal, TextInput, View, Select, Button
from sqlalchemy import select
from app.db.db_manager import get_session
from app.db.repositories import GameRepo, ArmyRepo
from app.db.models import User, Fief, Army  # Ensure Army is imported
from app.services.warfare_service import WarfareService


# ============================================================
#               STEP 3: PROVISIONING MODAL (GM)
# ============================================================
# No changes are needed in this modal. It receives the gold amount from the previous step.
class GMSailCargoModal(Modal):
    def __init__(self, bot, fleet, target_house_id, setup_data):
        super().__init__(title=f"GM: Provision House {target_house_id}")
        self.bot = bot
        self.fleet = fleet
        self.target_house_id = target_house_id
        self.setup_data = setup_data

    cargo = TextInput(
        label="Crew/Troops to Load",
        placeholder="1000, or inf:500 cav:200",
        required=False,
    )
    commander = TextInput(
        label="Commander Name (Optional)",
        placeholder="Leave blank for default",
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "⚙️ GM Override: Charting course...", ephemeral=True
        )
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, interaction.guild.id)
            gm_user = await session.scalar(
                select(User).where(User.discord_id == interaction.user.id)
            )
            if not game or not gm_user:
                return await interaction.followup.send(
                    "❌ Database Error.", ephemeral=True
                )

            service = WarfareService(session)
            success, result, fog_msg = await service.sail_fleet(
                game_id=game.game_id,
                user_id=gm_user.user_id,
                fleet_id=self.fleet.army_id,
                ships_input=self.setup_data["ships"],
                dest_name=self.setup_data["destination"],
                units_input=self.cargo.value,
                commander=self.commander.value or None,
                gold_to_carry=self.setup_data["gold"],
                waypoints=self.setup_data["waypoints"],
                is_gm_override=True,
                acting_house_id=self.target_house_id,
            )
            await send_gm_sail_feedback(
                interaction, success, result, fog_msg, self.target_house_id
            )


# ============================================================
#               STEP 2.5: CONTINUE VIEW (GM)
# ============================================================
# No changes are needed in this view.
class GMSailContinueView(View):
    def __init__(self, bot, fleet, target_house_id, setup_data):
        super().__init__(timeout=60)
        self.bot = bot
        self.fleet = fleet
        self.target_house_id = target_house_id
        self.setup_data = setup_data

    @discord.ui.button(
        label="Load Crew & Launch", style=discord.ButtonStyle.primary, emoji="📦"
    )
    async def continue_button(self, interaction: discord.Interaction, button: Button):
        modal = GMSailCargoModal(
            self.bot, self.fleet, self.target_house_id, self.setup_data
        )
        await interaction.response.send_modal(modal)
        self.stop()
        button.disabled = True
        await interaction.edit_original_response(view=self)


# ============================================================
#               STEP 2: SETUP MODAL (GM)
# ============================================================


# ============================================================
#               STEP 2: SETUP MODAL (GM)
# ============================================================
class GMSailSetupModal(Modal):
    def __init__(self, bot, fleet: Army, target_house_id: int, ship_capacity: int):
        super().__init__(title=f"GM Sail: House {target_house_id}")
        self.bot = bot
        self.fleet = fleet
        self.target_house_id = target_house_id
        self.has_cargo = bool(fleet.cargo and fleet.cargo.get("troop_count", 0) > 0)

        # --- FIX: DEFINE UI ELEMENTS FIRST ---
        self.ships = TextInput(
            label=f"Ships (Cap: {ship_capacity})",
            placeholder=f"all, or number (Max {fleet.troop_count})",
            default="all",
            required=True,
        )
        self.gold = TextInput(
            label="Gold (Optional)", placeholder="0", default="0", required=False
        )
        self.destination = TextInput(
            label="Destination", placeholder="Name or Coords", required=True
        )
        self.waypoints = TextInput(
            label="Waypoints (; separated)",
            placeholder="Dragonstone; 555,888",
            required=False,
        )
        # --- END FIX ---

        # Now that self.gold exists, we can safely apply the lock logic
        self.is_gold_locked = False
        if fleet.treasury and fleet.treasury > 0:
            self.gold.placeholder = f"Fleet already carrying {fleet.treasury} gold."
            self.gold.default = str(fleet.treasury)
            self.gold.disabled = True
            self.is_gold_locked = True

        # Add items to the view
        self.add_item(self.ships)
        self.add_item(self.gold)
        self.add_item(self.destination)
        self.add_item(self.waypoints)

    async def on_submit(self, interaction: discord.Interaction):
        gold_amount = 0
        if not self.is_gold_locked:
            try:
                gold_amount = int(self.gold.value or 0)
                if gold_amount < 0:
                    raise ValueError
            except ValueError:
                return await interaction.response.send_message(
                    "❌ Invalid Gold.", ephemeral=True
                )

        setup_data = {
            "ships": self.ships.value,
            "gold": gold_amount,
            "destination": self.destination.value,
            "waypoints": self.waypoints.value or None,
        }

        # PATH A: Fleet has cargo -> Sail immediately
        if self.has_cargo:
            await interaction.response.send_message(
                "⚙️ GM Override: Charting course...", ephemeral=True
            )
            async with get_session() as session:
                game = await GameRepo.get_active_game(session, interaction.guild.id)
                gm_user = await session.scalar(
                    select(User).where(User.discord_id == interaction.user.id)
                )
                service = WarfareService(session)

                success, result, fog_msg = await service.sail_fleet(
                    game_id=game.game_id,
                    user_id=gm_user.user_id,
                    fleet_id=self.fleet.army_id,
                    ships_input=setup_data["ships"],
                    dest_name=setup_data["destination"],
                    units_input=None,  # Use existing cargo
                    commander=None,
                    gold_to_carry=setup_data["gold"],
                    waypoints=setup_data["waypoints"],
                    is_gm_override=True,
                    acting_house_id=self.target_house_id,
                )
                await send_gm_sail_feedback(
                    interaction, success, result, fog_msg, self.target_house_id
                )
            return

        # PATH B: Fleet is empty -> Always proceed to cargo selection for GM
        # The previous check for 'is_friendly' is removed for GM override.
        view = GMSailContinueView(
            self.bot, self.fleet, self.target_house_id, setup_data
        )
        await interaction.response.send_message(
            f"⚓ **GM Logistics:** Fleet is empty. Provision House {self.target_house_id} troops and cargo.",
            view=view,
            ephemeral=True,
        )


# ============================================================
#               STEP 1: SELECT VIEW (GM)
# ============================================================
# No changes needed here, as it already passes the full fleet object.
class GMFleetSelectView(View):
    def __init__(self, bot, fleets, target_house_id):
        super().__init__(timeout=120)
        self.bot = bot
        self.target_house_id = target_house_id

        options = []
        for fleet in fleets[:25]:
            status_emoji = "⚓" if fleet.status == "DOCKED" else "🌊"
            loc_str = f"{int(fleet.location_x)}, {int(fleet.location_y)}"
            options.append(
                discord.SelectOption(
                    label=f"{fleet.commander_name} ({fleet.troop_count} ships)",
                    description=f"ID: {fleet.army_id} | {status_emoji} Loc: {loc_str}",
                    value=str(fleet.army_id),
                )
            )

        self.select_menu = Select(
            placeholder=f"Select House {target_house_id} Fleet...", options=options
        )
        self.select_menu.callback = self.on_select
        self.add_item(self.select_menu)

    async def on_select(self, interaction: discord.Interaction):
        fleet_id = int(self.select_menu.values[0])
        async with get_session() as session:
            fleet_obj = await ArmyRepo.get_army_by_id(session, fleet_id)
            game = await GameRepo.get_active_game(session, interaction.guild.id)
            if not fleet_obj or not game:
                return await interaction.response.send_message(
                    "❌ Error: Not found.", ephemeral=True
                )

            modal = GMSailSetupModal(
                self.bot, fleet_obj, self.target_house_id, game.ship_capacity
            )
            await interaction.response.send_modal(modal)

        self.stop()
        await interaction.message.delete()


# ============================================================
#               HELPER: SEND FEEDBACK
# ============================================================
# No changes needed in this helper function.
async def send_gm_sail_feedback(interaction, success, result, fog_msg, house_id):
    if not success:
        return await interaction.followup.send(
            f"❌ GM Command Failed: {result}", ephemeral=True
        )

    embed = discord.Embed(
        title=f"✅ GM Sail Order: House {house_id}",
        description=result.get("journey_summary", "Orders executed."),
        color=discord.Color.green(),
    )
    embed.add_field(name="Commander", value=result["commander"], inline=True)
    embed.add_field(name="Total Troops", value=str(result["count"]), inline=True)
    if result.get("gold_carried", 0) > 0:
        embed.add_field(name="Gold", value=f"💰 {result['gold_carried']}", inline=True)
    embed.add_field(name="ETA", value=result["time"], inline=False)

    if result.get("image"):
        file = discord.File(result["image"], filename="gm_sail.png")
        embed.set_image(url="attachment://gm_sail.png")
        await interaction.followup.send(file=file, embed=embed, ephemeral=False)
        if hasattr(result["image"], "close"):
            result["image"].close()
    else:
        await interaction.followup.send(embed=embed, ephemeral=False)

    if fog_msg:
        gen_channel = discord.utils.get(
            interaction.guild.text_channels, name="general-movements"
        )
        if gen_channel:
            await gen_channel.send(fog_msg)


class DirectGMSailView(View):
    """
    Shows a button to configure a specific fleet directly.
    Triggered by: !gm_war sail [HouseID] [FleetID]
    """

    def __init__(self, bot, fleet, target_house_id, ship_capacity):
        super().__init__(timeout=60)
        self.bot = bot
        self.fleet = fleet
        self.target_house_id = target_house_id
        self.ship_capacity = ship_capacity

    @discord.ui.button(
        label="Configure Sail Orders", style=discord.ButtonStyle.primary, emoji="⚓"
    )
    async def configure(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ GM Only.", ephemeral=True
            )

        modal = GMSailSetupModal(
            self.bot, self.fleet, self.target_house_id, self.ship_capacity
        )
        await interaction.response.send_modal(modal)
        self.stop()
        button.disabled = True
        await interaction.edit_original_response(
            content=f"📝 GM Configuring **{self.fleet.commander_name}**...", view=self
        )
