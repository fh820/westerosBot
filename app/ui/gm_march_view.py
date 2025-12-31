# import discord
# from discord.ui import Modal, TextInput, View, Select
# from sqlalchemy import select
# from app.db.db_manager import get_session
# from app.db.repositories import GameRepo
# from app.db.models import User
# from app.services.warfare_service import WarfareService


# class GMMarchModal(Modal):
#     def __init__(self, bot, army_id: int, target_house_id: int):
#         super().__init__(title=f"GM Override: House {target_house_id}")
#         self.bot = bot
#         self.army_id = army_id
#         self.target_house_id = target_house_id

#     # ... (Your UI Fields: destination, units, commander, gold, waypoints remain the same) ...
#     destination = TextInput(
#         label="Destination", placeholder="Fief name or Coords", required=True
#     )
#     units = TextInput(
#         label="Units (Override)",
#         placeholder="all, 1000, or inf:500",
#         default="all",
#         required=True,
#     )
#     commander = TextInput(
#         label="Commander (Optional)",
#         placeholder="Leave blank to keep current.",
#         required=False,
#     )
#     gold = TextInput(
#         label="Gold to Carry", placeholder="0", default="0", required=False
#     )
#     waypoints = TextInput(
#         label="Waypoints (; separated)",
#         placeholder="Moat Cailin; The Twins",
#         required=False,
#     )

#     async def on_submit(self, interaction: discord.Interaction):
#         await interaction.response.send_message(
#             "⚙️ GM Override: Calculating route...", ephemeral=True
#         )

#         try:
#             gold_amount = int(self.gold.value or 0)
#             if gold_amount < 0:
#                 raise ValueError
#         except ValueError:
#             return await interaction.followup.send(
#                 "❌ Gold must be a positive number.", ephemeral=True
#             )

#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, interaction.guild.id)
#             gm_user = await session.scalar(
#                 select(User).where(User.discord_id == interaction.user.id)
#             )

#             if not game or not gm_user:
#                 return await interaction.followup.send(
#                     "❌ Database Error.", ephemeral=True
#                 )

#             service = WarfareService(session)

#             success, result, fog_msg = await service.march_army(
#                 game_id=game.game_id,
#                 user_id=gm_user.user_id,
#                 identifier=str(self.army_id),
#                 dest_name=self.destination.value,
#                 units_input=self.units.value,
#                 commander=self.commander.value or None,
#                 gold_to_carry=gold_amount,
#                 waypoints=self.waypoints.value or None,
#                 is_gm_override=True,
#                 acting_house_id=self.target_house_id,
#             )

#             if not success:
#                 return await interaction.followup.send(
#                     f"❌ Error: {result}", ephemeral=True
#                 )

#             # 1. GM FEEDBACK (Private/Interactive Response)
#             embed = discord.Embed(
#                 title=f"✅ GM March Order: House {self.target_house_id}",
#                 color=discord.Color.green(),
#             )
#             embed.add_field(name="Commander", value=result["commander"], inline=True)
#             embed.add_field(name="Troops", value=str(result["count"]), inline=True)
#             embed.add_field(
#                 name="Gold", value=f"💰 {result.get('gold_carried', 0)}", inline=True
#             )
#             embed.add_field(name="ETA", value=result["time"], inline=False)

#             if result.get("image"):
#                 file = discord.File(result["image"], filename="gm_route.png")
#                 embed.set_image(url="attachment://gm_route.png")
#                 await interaction.followup.send(file=file, embed=embed, ephemeral=False)
#                 result["image"].close()
#             else:
#                 await interaction.followup.send(embed=embed, ephemeral=False)

#             # 2. PUBLIC FOG OF WAR (Corrected to general-movements)
#             if fog_msg:
#                 # FIX: Send to general-movements, NOT gm-alerts
#                 gen_channel = discord.utils.get(
#                     interaction.guild.text_channels, name="general-movements"
#                 )
#                 if gen_channel:
#                     # FIX: Do not prefix with "FOW Report for GM". Just send the rumor.
#                     await gen_channel.send(fog_msg)


# class GMMarchArmySelectView(View):
#     def __init__(self, bot, armies, target_house_id):
#         super().__init__(timeout=120)
#         self.bot = bot
#         self.target_house_id = target_house_id

#         options = []
#         for army in armies[:25]:
#             # FIX: Use the specific coordinate columns from your Army model
#             loc_str = f"{int(army.location_x)}, {int(army.location_y)}"

#             options.append(
#                 discord.SelectOption(
#                     label=f"{army.commander_name} ({army.troop_count})",
#                     description=f"ID: {army.army_id} | Loc: {loc_str}",
#                     value=str(army.army_id),
#                 )
#             )

#         self.select_menu = Select(
#             placeholder=f"Select House {target_house_id} Army...", options=options
#         )
#         self.select_menu.callback = self.on_select
#         self.add_item(self.select_menu)

#     async def on_select(self, interaction: discord.Interaction):
#         army_id = int(self.select_menu.values[0])
#         modal = GMMarchModal(self.bot, army_id, self.target_house_id)
#         await interaction.response.send_modal(modal)
#         self.stop()
#         await interaction.message.delete()


import discord
from discord.ui import Modal, TextInput, View, Select
from sqlalchemy import select
from app.db.db_manager import get_session
from app.db.repositories import GameRepo
from app.db.models import User, Army  # Make sure Army is imported
from app.services.warfare_service import WarfareService


class GMMarchModal(Modal):
    # --- FIX 1: Update the initializer to accept the full Army object ---
    def __init__(self, bot, army: Army, target_house_id: int):
        super().__init__(title=f"GM Override: House {target_house_id}")
        self.bot = bot
        self.army_id = army.army_id  # Store the ID for submission
        self.target_house_id = target_house_id

        # --- FIX 2: Create a state flag and disable the gold input if necessary ---
        self.is_gold_locked = False
        if army.treasury and army.treasury > 0:
            self.gold.placeholder = f"Army is already carrying {army.treasury} gold."
            self.gold.default = str(army.treasury)
            self.gold.disabled = True
            self.is_gold_locked = True

    destination = TextInput(
        label="Destination", placeholder="Fief name or Coords", required=True
    )
    units = TextInput(
        label="Units (Override)",
        placeholder="all, 1000, or inf:500",
        default="all",
        required=True,
    )
    commander = TextInput(
        label="Commander (Optional)",
        placeholder="Leave blank to keep current.",
        required=False,
    )
    gold = TextInput(
        label="Gold to Carry", placeholder="0", default="0", required=False
    )
    waypoints = TextInput(
        label="Waypoints (; separated)",
        placeholder="Moat Cailin; The Twins",
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "⚙️ GM Override: Calculating route...", ephemeral=True
        )

        # --- FIX 3: Check the state flag before processing new gold ---
        gold_amount = 0
        if not self.is_gold_locked:
            try:
                gold_amount = int(self.gold.value or 0)
                if gold_amount < 0:
                    raise ValueError
            except ValueError:
                return await interaction.followup.send(
                    "❌ Gold must be a positive number.", ephemeral=True
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

            success, result, fog_msg = await service.march_army(
                game_id=game.game_id,
                user_id=gm_user.user_id,
                identifier=str(self.army_id),
                dest_name=self.destination.value,
                units_input=self.units.value,
                commander=self.commander.value or None,
                gold_to_carry=gold_amount,  # This correctly sends only the NEW gold amount
                waypoints=self.waypoints.value or None,
                is_gm_override=True,
                acting_house_id=self.target_house_id,
            )

            if not success:
                return await interaction.followup.send(
                    f"❌ Error: {result}", ephemeral=True
                )

            embed = discord.Embed(
                title=f"✅ GM March Order: House {self.target_house_id}",
                color=discord.Color.green(),
            )
            embed.add_field(name="Commander", value=result["commander"], inline=True)
            embed.add_field(name="Troops", value=str(result["count"]), inline=True)
            if result.get("gold_carried", 0) > 0:
                embed.add_field(
                    name="Gold Carried",
                    value=f"💰 {result.get('gold_carried', 0)}",
                    inline=True,
                )
            embed.add_field(name="ETA", value=result["time"], inline=False)

            if result.get("image"):
                file = discord.File(result["image"], filename="gm_route.png")
                embed.set_image(url="attachment://gm_route.png")
                await interaction.followup.send(file=file, embed=embed, ephemeral=False)
                # It's good practice to close the file handle if it's opened in the service
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


class GMMarchArmySelectView(View):
    # --- FIX 4: The view now accepts and stores the full Army objects ---
    def __init__(self, bot, armies: list[Army], target_house_id: int):
        super().__init__(timeout=120)
        self.bot = bot
        self.target_house_id = target_house_id
        # Create a dictionary for easy lookup
        self.armies_map = {army.army_id: army for army in armies}

        options = []
        for army in armies[:25]:
            loc_str = f"{int(army.location_x)}, {int(army.location_y)}"
            options.append(
                discord.SelectOption(
                    label=f"{army.commander_name} ({army.troop_count})",
                    description=f"ID: {army.army_id} | Loc: {loc_str}",
                    value=str(army.army_id),
                )
            )

        self.select_menu = Select(
            placeholder=f"Select House {target_house_id} Army...", options=options
        )
        self.select_menu.callback = self.on_select
        self.add_item(self.select_menu)

    async def on_select(self, interaction: discord.Interaction):
        army_id = int(self.select_menu.values[0])

        # --- FIX 5: Retrieve the full object and pass it to the modal ---
        army_to_command = self.armies_map.get(army_id)
        if not army_to_command:
            return await interaction.response.send_message(
                "Error: Army not found.", ephemeral=True
            )

        modal = GMMarchModal(
            bot=self.bot, army=army_to_command, target_house_id=self.target_house_id
        )
        await interaction.response.send_modal(modal)

        self.stop()
        await interaction.message.delete()
