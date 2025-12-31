# # import discord
# # from discord.ext import commands, tasks
# # from sqlalchemy import select
# # from sqlalchemy.orm import selectinload
# # import asyncio
# # import json
# # import os
# # import re
# # import datetime
# # import redis.asyncio as redis
# # from app.db.db_manager import get_session
# # from app.db.repositories import GameRepo, ArmyRepo, FiefRepo
# # from app.services.warfare_service import WarfareService
# # from app.db.models import User, Army, GamePlayer, House
# # from app.tasks.heavy_tasks import generate_path_async
# # from app.tasks.light_tasks import resolve_army_arrival
# # from app.ui.march_view import ArmySelectView, JourneyArmySelectView
# # from app.ui.paginator import Paginator
# # from app.ui.sail_view import FleetSelectView
# # from app.services.warfare_service import FOG_OF_WAR_THRESHOLD
# # from app.ui.redirect_view import RedirectSelectView
# # from app.ui.coalition_view import CoalitionConsentView
# # from app.checks import is_in_house_channel, recruitment_is_enabled
# # from app.ui.gate_view import GateActionView
# # from app.tasks.light_tasks import handle_gate_response


# import discord
# from discord.ext import commands, tasks
# from sqlalchemy import select
# from sqlalchemy.orm import selectinload
# import asyncio
# import json
# import os
# import datetime
# import redis.asyncio as redis

# from app.db.db_manager import get_session
# from app.db.models import User, Army, GamePlayer, House, PendingInteraction
# from app.db.repositories import GameRepo, ArmyRepo, FiefRepo

# # from app.services.warfare_service import WarfareService, FOG_OF_WAR_THRESHOLD
# from app.tasks.light_tasks import resolve_army_arrival, handle_gate_response
# from app.ui.march_view import ArmySelectView, JourneyArmySelectView
# from app.ui.paginator import Paginator
# from app.ui.sail_view import FleetSelectView
# from app.ui.redirect_view import RedirectSelectView
# from app.ui.coalition_view import CoalitionConsentView
# from app.ui.gate_view import GateActionView
# from app.ui.interaction_view import InteractionView
# from app.ui.autobattle_view import AutoBattleControlView
# from app.checks import is_in_house_channel, recruitment_is_enabled
# from app.services.warfare_service import WarfareService, FOG_OF_WAR_THRESHOLD

# fief_cache = {}


# async def get_discord_user_from_house(self, session, game_id, house_id):
#     """Helper to find the Discord User associated with a House ID."""
#     stmt = (
#         select(User)
#         .join(GamePlayer)
#         .where(
#             GamePlayer.game_id == game_id,
#             GamePlayer.claimed_house_id == house_id,
#             GamePlayer.is_active == True,
#         )
#     )
#     user_db = (await session.execute(stmt)).scalars().first()
#     if user_db:
#         return self.bot.get_user(user_db.discord_id)
#     return None


# # --- UTILITY FUNCTIONS ---
# def parse_loose_json(text: str) -> dict:
#     """
#     A robust parser that handles unquoted keys AND values.
#     Input:  { from: Winterfell, to: "Moat Cailin", units: {inf: 100} }
#     Output: {'from': 'Winterfell', 'to': 'Moat Cailin', 'units': {'inf': 100}}
#     """
#     text = text.strip()
#     # Remove outer braces if present
#     if text.startswith("{") and text.endswith("}"):
#         text = text[1:-1]

#     # 1. Split by comma, respecting nested braces
#     items = []
#     buffer = ""
#     depth = 0
#     for char in text:
#         if char == "{":
#             depth += 1
#         elif char == "}":
#             depth -= 1

#         if char == "," and depth == 0:
#             items.append(buffer.strip())
#             buffer = ""
#         else:
#             buffer += char
#     if buffer.strip():
#         items.append(buffer.strip())

#     result = {}
#     for item in items:
#         if ":" not in item:
#             continue

#         # Split into Key and Value
#         key, val = item.split(":", 1)
#         key = key.strip().strip('"').strip("'")  # Clean key
#         val = val.strip()

#         # Logic to determine Value Type

#         # A. Nested Object (Recursion)
#         if val.startswith("{"):
#             val = parse_loose_json(val)
#         # B. Integer
#         elif val.isdigit():
#             val = int(val)
#         # C. Boolean
#         elif val.lower() == "true":
#             val = True
#         elif val.lower() == "false":
#             val = False
#         # D. String (Remove quotes if they exist, keep raw text if they don't)
#         else:
#             if (val.startswith('"') and val.endswith('"')) or (
#                 val.startswith("'") and val.endswith("'")
#             ):
#                 val = val[1:-1]

#         result[key] = val

#     return result


# def calculate_army_size_from_units(units_input) -> int:
#     """Calculates total army size from various unit input formats."""
#     if isinstance(units_input, int):
#         return units_input
#     if isinstance(units_input, str) and units_input.isdigit():
#         return int(units_input)
#     if isinstance(units_input, dict):
#         return sum(filter(lambda v: isinstance(v, int), units_input.values()))
#     return 1000  # Default fallback if unknown


# # --- MAIN COG ---
# class WarfareCog(commands.Cog):
#     def __init__(self, bot):
#         self.bot = bot
#         self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
#         # # self.redis = None
#         # self.redis_listener.start()
#         self.redis = None  # Initialize as None
#         self.pubsub = None
#         # Connect and start the listener task
#         asyncio.create_task(self.connect_and_listen())

#     # def cog_unload(self):
#     #     self.redis_listener.cancel()
#     def cog_unload(self):
#         # Gracefully disconnect and cancel the task when the cog is unloaded
#         self.redis_listener.cancel()
#         if self.redis:
#             asyncio.create_task(self.redis.close())

#     async def connect_and_listen(self):
#         """
#         Keeps a persistent connection to Redis and restarts the listener on disconnect.
#         This is the main connection management loop.
#         """
#         await self.bot.wait_until_ready()
#         while not self.bot.is_closed():
#             try:
#                 print("🟢 Connecting to Redis...")
#                 # Establish the connection ONCE
#                 self.redis = redis.from_url(self.redis_url, decode_responses=True)
#                 self.pubsub = self.redis.pubsub()
#                 await self.pubsub.subscribe("westeros_bot_events")
#                 print("✅ Redis Listener Subscribed.")
#                 # Start the actual message listener task
#                 self.redis_listener.start()
#                 # The loop will now be running in the background. This task's job is done.
#                 return

#             except redis.exceptions.ConnectionError as e:
#                 print(f"⚠️ Could not connect to Redis: {e}. Retrying in 10 seconds...")
#                 # If connection fails, stop the listener task if it's running
#                 if self.redis_listener.is_running():
#                     self.redis_listener.stop()
#                 if self.redis:
#                     await self.redis.close()  # Clean up the failed connection
#                 self.redis = None
#                 self.pubsub = None
#                 await asyncio.sleep(10)  # Wait before retrying

#     @tasks.loop(seconds=1.0)  # The interval here is just a fallback. listen() blocks.
#     async def redis_listener(self):
#         """
#         This task's ONLY job is to listen for messages on the established connection.
#         It should NOT create a new connection.
#         """
#         try:
#             # This efficiently waits here until a message arrives.
#             async for message in self.pubsub.listen():
#                 if message and message["type"] == "message":
#                     # Offload processing to a background task
#                     asyncio.create_task(self.process_message(message["data"]))
#         except Exception as e:
#             # If any error happens here (like a disconnect), we stop the loop.
#             # The outer `connect_and_listen` loop will detect this and try to reconnect.
#             print(f"⚠️ Redis listener error: {e}. Attempting to reconnect...")
#             self.redis_listener.stop()  # Stop this loop
#             # Trigger the reconnection logic by calling the main connection function again
#             asyncio.create_task(self.connect_and_listen())

#     async def process_message(self, raw_data):
#         """Processes the message without blocking the listener."""
#         try:
#             data = json.loads(raw_data)
#             if data["type"] == "ARRIVAL":
#                 await self.handle_arrival_notification(data)
#             elif data["type"] == "INTERCEPTION":
#                 await self.handle_interception_notification(data)
#             elif data["type"] in ["PATH_READY", "PATH_FAILED"]:
#                 await self.handle_path_notification(data)
#             elif data["type"] == "BANKRUPTCY_ALERT":
#                 await self.handle_bankruptcy_notification(data)
#             elif data["type"] == "BANNER_REPORT":
#                 await self.handle_banner_report(data)
#             elif data["type"] == "GATE_ALERT":  # <--- NEW HANDLER
#                 await self.handle_gate_alert(data)
#             elif data["type"] == "GATE_RESPONSE":
#                 handle_gate_response.delay(data["army_id"], data["action"])
#             elif data["type"] == "PASSAGE_DENIED":
#                 await self.handle_passage_denied(data)
#             elif data["type"] == "PROMPT_INTERACTION":
#                 await self.handle_prompt_interaction(data)
#             elif data["type"] in [
#                 "INTERACTION_BATTLE",
#                 "INTERACTION_MEETING",
#                 "INTERACTION_ENDED",
#             ]:
#                 await self.handle_interaction_resolution(data)
#             elif data["type"] == "PROMPT_AUTOBATTLE":
#                 await self.handle_prompt_autobattle(data)
#             elif data["type"] == "BATTLE_REPORT_ROUND":
#                 await self.handle_battle_round_report(data)
#             elif data["type"] == "BATTLE_REPORT_FINAL":
#                 await self.handle_battle_final_report(data)
#             elif data["type"] == "BATTLE_STARTED":
#                 await self.handle_battle_started(data)
#         except Exception as e:
#             print(f"❌ Error processing Redis message: {e}")

#     # In app/cogs/warfare_cog.py -> inside the WarfareCog class
#     async def handle_prompt_autobattle(self, data):
#         """
#         Receives the prompt from the Celery worker and posts the GM choice UI.
#         """
#         guild = self.bot.get_guild(data.get("guild_id"))
#         if not guild:
#             return

#         gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
#         if not gm_channel:
#             print(
#                 "ERROR: Could not find #gm-alerts channel to post auto-battle prompt."
#             )
#             return

#         embed = discord.Embed(
#             title="🛡️ Auto-Battle Pending",
#             description="A battle has been triggered by player actions. You have **15 minutes** to intervene before the auto-battle begins.",
#             color=discord.Color.dark_red(),
#         )
#         embed.add_field(
#             name="Attacker", value=data.get("attacker_name", "Unknown"), inline=True
#         )
#         embed.add_field(
#             name="Defender", value=data.get("defender_name", "Unknown"), inline=True
#         )
#         embed.set_footer(text=f"Battle ID: {data.get('battle_id')}")

#         view = AutoBattleControlView(
#             battle_id=data.get("battle_id"),
#             resolver_task_id=data.get("resolver_task_id"),
#         )

#         gm_role = discord.utils.get(guild.roles, name="Game Master")
#         mention = gm_role.mention if gm_role else ""

#         try:
#             await gm_channel.send(content=f"🚨 {mention}", embed=embed, view=view)
#         except Exception as e:
#             print(f"Error sending auto-battle prompt to GM channel: {e}")

#     async def handle_battle_started(self, data):
#         """Posts an announcement that an auto-battle has begun."""
#         guild = self.bot.get_guild(data.get("guild_id"))
#         if not guild:
#             return

#         battle_channel = discord.utils.get(guild.text_channels, name="battle-reports")
#         if not battle_channel:
#             return

#         embed = discord.Embed(
#             title="⚔️ A Skirmish Has Erupted!",
#             description=f"Forces from **House {data['attacker_house']}** under **{data['attacker_name']}** have engaged with the army of **House {data['defender_house']}** led by **{data['defender_name']}**!",
#             color=discord.Color.dark_orange(),
#         )
#         embed.set_footer(
#             text=f"Battle ID: {data.get('battle_id')}. Reports will follow."
#         )

#         try:
#             await battle_channel.send(embed=embed)
#         except discord.Forbidden:
#             print(f"ERROR: Bot lacks permission to post in #{battle_channel.name}")

#     async def handle_battle_round_report(self, data):
#         """Posts a simplified result of a single auto-battle round with NO casualty fields."""
#         guild = self.bot.get_guild(data.get("guild_id"))
#         if not guild:
#             return

#         battle_channel = discord.utils.get(guild.text_channels, name="battle-reports")
#         if not battle_channel:
#             return

#         scores = data.get("scores", {})

#         # --- CORRECTED EMBED: No "Attacker/Defender Losses" fields ---
#         embed = discord.Embed(
#             title=f"⚔️ Round {data.get('round_number', '?')} Analysis ({scores.get('attacker',0)} - {scores.get('defender',0)})",
#             description=data.get("roll_msg", "Roll details unavailable."),
#             color=discord.Color.dark_grey(),
#         )
#         embed.set_footer(text=f"Battle ID: {data.get('battle_id')}")

#         try:
#             await battle_channel.send(embed=embed)
#         except discord.Forbidden:
#             print(f"ERROR: Bot lacks permission to post in #{battle_channel.name}")

#     # =========================================================
#     # ===== START: THE CORRECTED FINAL REPORT HANDLER     =====
#     # =========================================================
#     async def handle_battle_final_report(self, data):
#         """Posts the final conclusion of an auto-battle."""
#         print("\n--- DEBUG: handle_battle_final_report START ---")
#         guild_id = data.get("guild_id")
#         battle_id = data.get("battle_id")
#         report_string = data.get("report_string")
#         print(
#             f"  - Received final report for Battle ID: {battle_id} in Guild ID: {guild_id}"
#         )

#         if not guild_id or not battle_id or not report_string:
#             print(
#                 "  - CRITICAL ERROR: Payload is missing guild_id, battle_id, or report_string. Aborting."
#             )
#             return

#         guild = self.bot.get_guild(guild_id)
#         if not guild:
#             print(
#                 f"  - CRITICAL ERROR: Bot could not find Guild with ID {guild_id}. Aborting."
#             )
#             return

#         print(f"  - Found Guild: '{guild.name}'")

#         battle_channel = discord.utils.get(guild.text_channels, name="battle-reports")
#         if not battle_channel:
#             print(
#                 f"  - CRITICAL ERROR: Could not find #battle-reports channel in '{guild.name}'. Aborting."
#             )
#             return

#         print(f"  - Found Channel: '#{battle_channel.name}'")

#         embed = discord.Embed(
#             title=f"🏁 Battle Concluded! (ID: {battle_id})",
#             description=report_string,
#             color=discord.Color.green(),
#         )

#         try:
#             await battle_channel.send(embed=embed)
#             print(f"  - SUCCESS: Posted final report for Battle ID: {battle_id}")
#         except discord.Forbidden:
#             print(
#                 f"  - DISCORD ERROR: Bot lacks permission to post final report in #{battle_channel.name}"
#             )
#         except Exception as e:
#             print(
#                 f"  - UNEXPECTED ERROR: An error occurred while posting the final report: {e}"
#             )

#         print("--- DEBUG: handle_battle_final_report END ---\n")

#     # =========================================================
#     # ===== END: THE CORRECTED FINAL REPORT HANDLER       =====
#     # =========================================================

#     # Now, create the new handler function itself within the WarfareCog
#     async def handle_passage_denied(self, payload: dict):
#         """
#         Notifies an attacker that their passage through a gate was denied.
#         """
#         guild = self.bot.get_guild(payload.get("guild_id"))
#         if not guild:
#             return

#         army_id = payload.get("army_id")
#         gate_name = payload.get("gate_name")
#         denied_by = payload.get("denied_by")

#         # We need to find the army's owner to find their channel
#         async with get_session() as session:
#             # Find the army and its owner's house
#             army = await session.get(Army, army_id, options=[selectinload(Army.house)])
#             if not army or not army.house:
#                 print(
#                     f"Could not find army or house for ID {army_id} to send denial notice."
#                 )
#                 return

#             # Find the player associated with that house
#             stmt = (
#                 select(GamePlayer)
#                 .join(User)
#                 .where(
#                     GamePlayer.claimed_house_id == army.house_id,
#                     GamePlayer.game_id == army.game_id,
#                     GamePlayer.is_primary == True,
#                 )
#                 .options(selectinload(GamePlayer.user))
#             )
#             player = (await session.execute(stmt)).scalars().first()

#             if not player or not player.user:
#                 # This was likely an NPC army, no player to notify
#                 return

#             # Find the player's private channel
#             attacker_house_name = army.house.name.lower().replace(" ", "-")
#             channel_name = f"{attacker_house_name}-quarters"
#             target_channel = discord.utils.get(guild.text_channels, name=channel_name)

#             if not target_channel:
#                 print(f"Could not find channel '{channel_name}' to notify attacker.")
#                 return

#             # Construct and send the notification
#             embed = discord.Embed(
#                 title="❌ March Halted: Passage Denied",
#                 description=f"Your army, **{army.commander_name}**, has been denied passage at **{gate_name}** by **{denied_by}**.",
#                 color=discord.Color.red(),
#             )
#             embed.set_footer(text="The army is now idle and awaits new orders.")

#             await target_channel.send(f"<@{player.user.discord_id}>", embed=embed)
#             print(
#                 f"Successfully notified attacker in #{target_channel.name} about denied passage."
#             )

#     # In app/cogs/warfare_cog.py -> WarfareCog class (add these new methods)

#     async def get_player_channel(self, guild: discord.Guild, house: House):
#         """
#         Helper to find a player's private house channel using a House object.
#         This version does NOT access the database.
#         """
#         if not house:
#             return None
#         channel_name = f"{house.name.lower().replace(' ', '-')}-quarters"
#         return discord.utils.get(guild.text_channels, name=channel_name)

#     async def handle_prompt_interaction(self, data):
#         """
#         Sends the UI prompt to both players involved in an interaction.
#         This version is refactored to prevent async DB conflicts.
#         """
#         interaction_id = data["interaction_id"]

#         # --- Phase 1: All Database Operations in One Block ---
#         async with get_session() as session:
#             # Eagerly load all the data we will possibly need
#             interaction = await session.get(
#                 PendingInteraction,
#                 interaction_id,
#                 options=[
#                     selectinload(PendingInteraction.army1)
#                     .selectinload(Army.house)
#                     .selectinload(House.game),
#                     selectinload(PendingInteraction.army2).selectinload(Army.house),
#                 ],
#             )

#             if not interaction or not interaction.army1 or not interaction.army2:
#                 print(
#                     f"DEBUG: Could not process prompt for interaction {interaction_id}. Invalid data."
#                 )
#                 return

#             # Store all the data we need in local variables before closing the session
#             guild_id = interaction.army1.house.game.guild_id
#             army1 = interaction.army1
#             army2 = interaction.army2

#             # Send the UI messages and then update the DB record with the message IDs
#             guild = self.bot.get_guild(guild_id)
#             if not guild:
#                 return

#             # --- Phase 2: All Discord Operations, No DB Access ---
#             embed = discord.Embed(
#                 title="⚔️ Army Contact Imminent!",
#                 description="Your forces are on a collision course with another army. You have **one hour** to issue orders before contact is made.",
#                 color=discord.Color.orange(),
#             )

#             # --- Prompt Player 1 (The Marcher) ---
#             army1_channel = await self.get_player_channel(guild, army1.house)
#             if army1_channel:
#                 embed.set_footer(
#                     text=f"Your Army: {army1.commander_name} | Opponent: {army2.commander_name}"
#                 )
#                 view1 = InteractionView(
#                     interaction_id=interaction.id, for_army_id=army1.army_id
#                 )
#                 try:
#                     msg1 = await army1_channel.send(embed=embed, view=view1)
#                     interaction.army1_channel_id = army1_channel.id
#                     interaction.army1_message_id = msg1.id
#                 except discord.Forbidden:
#                     print(
#                         f"ERROR: Bot does not have permission to send messages in #{army1_channel.name}"
#                     )

#             # --- Prompt Player 2 (The Target) ---
#             army2_channel = await self.get_player_channel(guild, army2.house)
#             if army2_channel:
#                 embed.set_footer(
#                     text=f"Your Army: {army2.commander_name} | Opponent: {army1.commander_name}"
#                 )
#                 view2 = InteractionView(
#                     interaction_id=interaction.id, for_army_id=army2.army_id
#                 )
#                 try:
#                     msg2 = await army2_channel.send(embed=embed, view=view2)
#                     interaction.army2_channel_id = army2_channel.id
#                     interaction.army2_message_id = msg2.id
#                 except discord.Forbidden:
#                     print(
#                         f"ERROR: Bot does not have permission to send messages in #{army2_channel.name}"
#                     )

#             # --- Phase 3: Final, Quick DB Update ---
#             if interaction.army1_message_id or interaction.army2_message_id:
#                 await session.commit()

#     # In app/cogs/warfare_cog.py

#     async def handle_interaction_resolution(self, data):
#         """
#         Updates the player UI and executes consequences after an interaction is resolved.
#         This version uses the reliable fetch_member to guarantee players are found.
#         """
#         interaction_id = data.get("interaction_id")
#         outcome = data.get("type")
#         if not interaction_id or not outcome:
#             return

#         async with get_session() as session:
#             interaction = await session.get(
#                 PendingInteraction,
#                 interaction_id,
#                 options=[
#                     selectinload(PendingInteraction.army1)
#                     .selectinload(Army.house)
#                     .selectinload(House.game),
#                     selectinload(PendingInteraction.army2).selectinload(Army.house),
#                 ],
#             )
#             if not interaction:
#                 return

#             async def get_player_for_house(house_id: int):
#                 stmt = (
#                     select(User)
#                     .join(GamePlayer, User.user_id == GamePlayer.user_id)
#                     .where(
#                         GamePlayer.claimed_house_id == house_id,
#                         GamePlayer.is_primary == True,
#                     )
#                 )
#                 user = (await session.execute(stmt)).scalar_one_or_none()
#                 return user

#             user1 = await get_player_for_house(interaction.army1.house_id)
#             user2 = await get_player_for_house(interaction.army2.house_id)

#             guild = self.bot.get_guild(interaction.army1.house.game.guild_id)
#             if not guild:
#                 return

#             # =========================================================
#             # ===== START: THE DEFINITIVE FIX (fetch_member)      =====
#             # =========================================================
#             member1, member2 = None, None
#             try:
#                 if user1 and user1.discord_id:
#                     member1 = await guild.fetch_member(user1.discord_id)
#                 if user2 and user2.discord_id:
#                     member2 = await guild.fetch_member(user2.discord_id)
#             except discord.NotFound:
#                 print(
#                     f"ERROR: A player's Discord ID from the database was not found in the Discord server for interaction {interaction_id}."
#                 )
#                 # We can't proceed if a member is missing for a meeting.
#                 return
#             except Exception as e:
#                 print(f"An unexpected Discord API error occurred: {e}")
#                 return
#             # =========================================================
#             # ===== END: THE DEFINITIVE FIX                       =====
#             # =========================================================

#             if outcome == "INTERACTION_MEETING":
#                 if not member1 or not member2:
#                     print(
#                         f"ERROR: Cannot create meeting for interaction {interaction_id}. One or both players not found."
#                     )
#                     return

#                 diplomacy_cog = self.bot.get_cog("DiplomacyCog")
#                 if not diplomacy_cog:
#                     print(
#                         "ERROR: DiplomacyCog not found. Cannot create meeting channel."
#                     )
#                     return

#                 location_string = f"The field near ({int(interaction.location_x)}, {int(interaction.location_y)})"
#                 meeting_channel = await diplomacy_cog._create_meeting_channel(
#                     guild=guild,
#                     member1=member1,
#                     member2=member2,
#                     location_str=location_string,
#                     name1=interaction.army1.house.name,
#                     name2=interaction.army2.house.name,
#                 )

#                 # (Future Step: Update the UI to confirm the meeting was created)
#                 if meeting_channel:
#                     print(
#                         f"Successfully created meeting channel: {meeting_channel.name}"
#                     )

#             elif outcome == "INTERACTION_BATTLE":
#                 print("BATTLE outcome detected. Auto-battle logic will go here.")

#             elif outcome == "INTERACTION_ENDED":
#                 print("MARCH ON outcome detected. No action taken.")

#     @commands.Cog.listener()
#     async def on_interaction(self, interaction: discord.Interaction):
#         """Listener for all button clicks, including our new interaction view."""
#         custom_id = interaction.data.get("custom_id")
#         if not custom_id or not custom_id.startswith("interaction_"):
#             # This is not one of our buttons, so we ignore it.
#             # We must make sure our other views (like diplomacy) are handled elsewhere.
#             return

#         # --- Parse the custom_id: interaction_{CHOICE}_{interaction_id}_{army_id} ---
#         try:
#             _, choice, interaction_id_str, army_id_str = custom_id.split("_")
#             interaction_id = int(interaction_id_str)
#             army_id = int(army_id_str)
#         except ValueError:
#             await interaction.response.send_message(
#                 "❌ Invalid button ID.", ephemeral=True
#             )
#             return

#         await interaction.response.defer()  # Acknowledge the click immediately

#         async with get_session() as session:
#             # Fetch the interaction from the database
#             pending_interaction = await session.get(
#                 PendingInteraction,
#                 interaction_id,
#                 options=[selectinload(PendingInteraction.army1)],
#             )

#             if not pending_interaction or pending_interaction.status != "PENDING":
#                 await interaction.followup.send(
#                     "This interaction has already been resolved or has expired.",
#                     ephemeral=True,
#                 )
#                 return

#             # Determine if this is army1 or army2 making the choice
#             if army_id == pending_interaction.army1_id:
#                 pending_interaction.army1_choice = choice
#             elif army_id == pending_interaction.army2_id:
#                 pending_interaction.army2_choice = choice
#             else:
#                 await interaction.followup.send(
#                     "Error: You do not command this army.", ephemeral=True
#                 )
#                 return

#             await session.commit()

#             # Give feedback and disable the buttons
#             await interaction.followup.send(
#                 f"✅ Your choice '{choice.replace('_', ' ')}' has been registered.",
#                 ephemeral=True,
#             )

#             # Disable the buttons on the message that was clicked.
#             original_view = InteractionView(interaction_id, army_id)
#             await original_view.disable_all_buttons()
#             await interaction.edit_original_response(view=original_view)

#     async def handle_gate_alert(self, payload: dict):
#         """
#         Processes a GATE_ALERT event and attaches interactive buttons.
#         """
#         guild = self.bot.get_guild(payload.get("guild_id"))
#         if not guild:
#             print(f"Could not find guild with ID: {payload.get('guild_id')}")
#             return

#         defender = payload.get("defender", {})
#         marcher = payload.get("marcher", {})
#         # --- WE NEED THE ARMY ID FROM THE PAYLOAD ---
#         # You will need to add this to your Celery task payload
#         attacking_army_id = payload.get("attacking_army_id")
#         if not attacking_army_id:
#             print(
#                 "CRITICAL: Gate alert received without an 'attacking_army_id'. Cannot create buttons."
#             )
#             return

#         target_channel = None
#         # ... (your existing channel finding logic is perfect, no changes needed) ...
#         if defender.get("is_npc"):
#             target_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
#         else:
#             house_name = defender.get("house_name", "").lower().replace(" ", "-")
#             expected_channel_name = f"{house_name}-quarters"
#             target_channel = discord.utils.get(
#                 guild.text_channels, name=expected_channel_name
#             )
#             if not target_channel:
#                 target_channel = discord.utils.get(
#                     guild.text_channels, name="gm-alerts"
#                 )

#         if not target_channel:
#             print(
#                 f"CRITICAL: Could not find any channel to post alert in for guild {guild.name}."
#             )
#             return

#         # Build the embed (your existing code is fine)
#         embed = discord.Embed(
#             title=f"⚔️ Gate Alert: {payload.get('gate_name')}",
#             description=f"An army approaches a strategic chokepoint you control!",
#             color=discord.Color.orange(),  # Use a neutral color initially
#         )
#         embed.add_field(
#             name="Attacking Army",
#             value=f"**{marcher.get('commander')}** of House **{marcher.get('house_name')}**",
#             inline=False,
#         )
#         embed.add_field(
#             name="Troop Count", value=f"{marcher.get('troops', 'N/A')}", inline=True
#         )
#         embed.set_footer(text="A decision is required: Grant or Deny Passage.")

#         ping_message = ""
#         defender_discord_id = defender.get("discord_id")
#         if not defender.get("is_npc") and defender_discord_id:
#             ping_message = f"<@{defender_discord_id}>"

#         # --- NEW: Create and pass the interactive view ---
#         view = GateActionView(
#             self.bot, guild.id, attacking_army_id, defender_discord_id
#         )

#         await target_channel.send(ping_message, embed=embed, view=view)
#         print(f"Successfully sent interactive gate alert to #{target_channel.name}")

#     async def handle_bankruptcy_notification(self, data):
#         """
#         Alerts GMs about houses running out of gold.
#         data = { guild_id, data: [{name, debt, troops}, ...] }
#         """
#         guild = self.bot.get_guild(data["guild_id"])
#         if not guild:
#             return

#         # Send to #gm-alerts
#         channel = discord.utils.get(guild.text_channels, name="gm-alerts")
#         if not channel:
#             return

#         embed = discord.Embed(
#             title="📉 Logistics Report: Bankruptcy",
#             description="The following houses cannot pay their daily army upkeep.",
#             color=discord.Color.dark_red(),
#         )

#         for entry in data["data"]:
#             # entry looks like: {'name': 'Stark', 'debt': -500, 'troops': 5000}
#             embed.add_field(
#                 name=f"House {entry['name']}",
#                 value=f"**Debt:** {entry['debt']} Gold\n**At Risk:** {entry['troops']} Troops",
#                 inline=False,
#             )

#         embed.set_footer(text="Use `!punish [House] [Percent]` to simulate desertion.")
#         await channel.send(embed=embed)

#     async def handle_banner_report(self, data):
#         """
#         Sends the Banner Report to the Liege's private channel.
#         """
#         guild = self.bot.get_guild(data["guild_id"])
#         if not guild:
#             return

#         # 1. Find the Liege's Private Channel
#         # Format: #stark-quarters
#         chan_name = f"{data['liege_house_name'].lower().replace(' ', '-')}-quarters"
#         channel = discord.utils.get(guild.text_channels, name=chan_name)

#         # Fallback: ID-based channel if name fails
#         if not channel:
#             channel = discord.utils.get(
#                 guild.text_channels, name=f"{data['owner_id']}-quarters"
#             )

#         if not channel:
#             print(f"❌ Could not find channel {chan_name} to post banner report.")
#             return

#         # 2. Build Embed
#         embed = discord.Embed(title="🦅 Banner Call Report", color=discord.Color.blue())

#         report_text = "\n".join(data["report_lines"])
#         if len(report_text) > 3000:
#             report_text = report_text[:3000] + "...(truncated)"

#         embed.description = report_text
#         unit_noun = "ships" if data.get("call_type") == "SEA" else "men"
#         embed.add_field(
#             name="Total Raised",
#             value=f"**{data['total_raised']}** {unit_noun}",
#             inline=True,
#         )
#         embed.add_field(
#             name="Full Assembly", value=f"**{data['max_duration']}**", inline=True
#         )

#         # 3. Send and Tag User
#         await channel.send(f"<@{data['owner_id']}>", embed=embed)

#     async def handle_arrival_notification(self, data):
#         """
#         Sends arrival embeds. The public log is now suppressed for small armies.
#         """
#         guild = self.bot.get_guild(data["guild_id"])
#         if not guild:
#             return

#         unit_noun = "men"
#         if data.get("unit_type") == "SEA":
#             unit_noun = "ships"

#         # 1. Private Notification (This ALWAYS happens)
#         chan_name_private = f"{data['house_name'].lower().replace(' ', '-')}-quarters"
#         private_channel = discord.utils.get(guild.text_channels, name=chan_name_private)

#         if private_channel and data["owner_id"]:
#             embed_private = discord.Embed(
#                 title="📍 Arrival Report",
#                 description=f"**{data['commander']}** ({data['troops']} {unit_noun}) has arrived at **{data['location']}**.",
#                 color=discord.Color.green(),
#             )
#             await private_channel.send(f"<@{data['owner_id']}>", embed=embed_private)

#         # --- THIS IS THE FIX ---
#         # 2. Public Notification (This is now CONDITIONAL)
#         # Only post to the public channel if the force is large enough.
#         if data["troops"] >= FOG_OF_WAR_THRESHOLD:
#             public_channel = discord.utils.get(
#                 guild.text_channels, name="army-movements"
#             )
#             if not public_channel:
#                 public_channel = discord.utils.get(
#                     guild.text_channels, name="general-movements"
#                 )

#             if public_channel:
#                 house_role = discord.utils.get(guild.roles, name=data["house_name"])
#                 mention = (
#                     house_role.mention
#                     if house_role
#                     else f"**House {data['house_name']}**"
#                 )

#                 public_msg = f"✅ The forces of {mention} under the command of **{data['commander']}** ({data['troops']} {unit_noun}) have arrived at **{data['location']}**."

#                 await public_channel.send(public_msg)

#     async def handle_path_notification(self, data):
#         guild = self.bot.get_guild(data["guild_id"])
#         channel = guild.get_channel(data["channel_id"]) if guild else None
#         user = guild.get_member(data["user_id"]) if guild else None

#         if not channel or not user:
#             return

#         if data["type"] == "PATH_FAILED":
#             await channel.send(
#                 f"{user.mention}, your journey plan failed: {data['reason']}"
#             )
#             return

#         try:
#             if os.path.exists(data["image_path"]):
#                 file = discord.File(data["image_path"], filename="journey.png")
#                 embed = discord.Embed(
#                     title=f"Journey Plan: {data['origin']} to {data['destination']}",
#                     description=f"Mode: **{data['mode']}**",
#                     color=discord.Color.blue(),
#                 )
#                 embed.add_field(
#                     name="Est. Travel Time", value=data["time"], inline=True
#                 )
#                 embed.add_field(
#                     name="Distance", value=f"~{data['distance']} miles", inline=True
#                 )
#                 embed.set_image(url="attachment://journey.png")

#                 await channel.send(content=f"{user.mention}", file=file, embed=embed)
#             else:
#                 await channel.send(
#                     f"{user.mention}, map generated but file not found on server."
#                 )
#         except Exception as e:
#             await channel.send(f"❌ Error sending map: {e}")

#     @redis_listener.before_loop
#     async def before_listener(self):
#         await self.bot.wait_until_ready()

#     @commands.command(name="fiefs", aliases=["locations", "places"])
#     @commands.check(is_in_house_channel)
#     async def list_all_fiefs(self, ctx):
#         """Displays a complete, paginated list of all known fiefs."""
#         async with ctx.typing():  # Shows "Bot is typing..." for a better user experience
#             async with get_session() as session:
#                 game = await GameRepo.get_active_game(session, ctx.guild.id)
#                 if not game:
#                     return await ctx.send("❌ No active game.")

#                 # 1. Fetch, de-duplicate, and sort all fief names
#                 all_fiefs_raw = await FiefRepo.get_all_fief_names(session, game.game_id)
#                 all_fiefs = sorted(list(set(all_fiefs_raw)))

#                 if not all_fiefs:
#                     return await ctx.send(
#                         "❌ No fiefs have been defined for this game."
#                     )

#                 # 2. Chunk the data into pages
#                 # We'll put 25 names per page to keep it clean.
#                 CHUNK_SIZE = 25
#                 fief_chunks = [
#                     all_fiefs[i : i + CHUNK_SIZE]
#                     for i in range(0, len(all_fiefs), CHUNK_SIZE)
#                 ]

#                 embeds = []
#                 total_pages = len(fief_chunks)

#                 # 3. Create an embed for each page
#                 for i, chunk in enumerate(fief_chunks):
#                     # Use a code block for clean, non-pinging text
#                     description = "```\n" + "\n".join(chunk) + "\n```"

#                     embed = discord.Embed(
#                         title="📜 List of Known Fiefs",
#                         description=description,
#                         color=discord.Color.blurple(),
#                     )
#                     embed.set_footer(text=f"Page {i + 1} of {total_pages}")
#                     embeds.append(embed)

#                 # 4. Send the paginator view
#                 if not embeds:
#                     return await ctx.send("Could not generate the fief list.")

#                 # If there's only one page, no need for buttons
#                 if len(embeds) == 1:
#                     await ctx.send(embed=embeds[0])
#                 else:
#                     view = Paginator(embeds)
#                     await ctx.send(embed=embeds[0], view=view)

#     @commands.command(name="journey", aliases=["plan"])
#     @commands.check(is_in_house_channel)
#     async def journey(self, ctx):
#         """Initiates the interactive journey planning UI for any army or fleet."""
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await ctx.send("❌ No active game.")

#             player = await session.scalar(
#                 select(GamePlayer)
#                 .join(User)
#                 .where(
#                     User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#                 )
#             )
#             if not player or not player.claimed_house_id:
#                 return await ctx.send("❌ You do not have a house to command.")

#             # --- THIS IS THE FIX ---
#             # Fetch ALL armies and fleets owned by the player, not just land armies.
#             available_units = (
#                 (
#                     await session.execute(
#                         select(Army).where(Army.house_id == player.claimed_house_id)
#                     )
#                 )
#                 .scalars()
#                 .all()
#             )
#             # --- END OF FIX ---

#             if not available_units:
#                 return await ctx.send(
#                     "You have no units to use as a starting point for a plan."
#                 )

#             view = JourneyArmySelectView(bot=self.bot, armies=available_units)
#             await ctx.send(
#                 "**Step 1: Select a starting unit for your journey plan.**",
#                 view=view,
#                 ephemeral=True,
#             )

#     @commands.command(name="march")
#     @commands.check(is_in_house_channel)
#     async def march(self, ctx):
#         """Initiates the interactive march order UI for LAND armies."""
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await ctx.send("❌ No active game.")

#             player = await session.scalar(
#                 select(GamePlayer)
#                 .join(User)
#                 .where(
#                     User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#                 )
#             )

#             if not player or not player.claimed_house_id:
#                 return await ctx.send("❌ You do not have a house to command.")

#             # Fetch only the user's available LAND armies
#             available_armies = (
#                 (
#                     await session.execute(
#                         select(Army).where(
#                             Army.house_id == player.claimed_house_id,
#                             Army.army_type == "LAND",
#                             Army.status.in_(["IDLE", "GARRISONED", "RETREATING"]),
#                         )
#                     )
#                 )
#                 .scalars()
#                 .all()
#             )

#             if not available_armies:
#                 return await ctx.send(
#                     "You have no idle land armies available to march."
#                 )

#             # Create and send the simple ArmySelectView.
#             # It no longer needs the giant list of fiefs.
#             view = ArmySelectView(bot=self.bot, armies=available_armies)

#             await ctx.send(
#                 "**Step 1: Select an army to move.**",
#                 view=view,
#                 ephemeral=True,
#             )

#     @commands.command(name="redirect")
#     @commands.check(is_in_house_channel)
#     async def redirect(self, ctx):
#         """Initiates the interactive UI to redirect a moving army or fleet."""
#         async with get_session() as session:
#             # 1. Validate Game and Player
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await ctx.send("❌ No active game found.")

#             player = await session.scalar(
#                 select(GamePlayer)
#                 .join(User)
#                 .where(
#                     User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#                 )
#             )

#             if not player or not player.claimed_house_id:
#                 return await ctx.send("❌ You do not have a house to command.")

#             # 2. Fetch ALL moving units (Marching or Sailing)
#             moving_units = (
#                 (
#                     await session.execute(
#                         select(Army).where(
#                             Army.house_id == player.claimed_house_id,
#                             Army.status.in_(["MARCHING", "SAILING"]),
#                         )
#                     )
#                 )
#                 .scalars()
#                 .all()
#             )

#             if not moving_units:
#                 return await ctx.send("You have no moving units to redirect.")

#             # 3. FILTER LOGIC: Hide "Ghost Armies"
#             # We filter out armies that have a departure_time in the FUTURE.
#             # These are armies currently sitting inside a boat, waiting for the
#             # fleet to land. The user should redirect the FLEET, not the cargo.

#             now = datetime.datetime.now(datetime.timezone.utc)
#             valid_units = []

#             for unit in moving_units:
#                 # Ensure the unit's time is timezone-aware for comparison
#                 dep_time = unit.departure_time
#                 if dep_time and dep_time.tzinfo is None:
#                     dep_time = dep_time.replace(tzinfo=datetime.timezone.utc)

#                 # If the army is scheduled to start marching in the future, skip it.
#                 if dep_time and dep_time > now:
#                     continue

#                 valid_units.append(unit)

#             # 4. Check if any units remain after filtering
#             if not valid_units:
#                 return await ctx.send(
#                     "You have no active moving units to redirect (units inside moving fleets cannot be redirected individually)."
#                 )

#             # 5. Launch the View with only valid units
#             view = RedirectSelectView(bot=self.bot, armies=valid_units)
#             await ctx.send(
#                 "**Step 1: Select a unit to redirect.**",
#                 view=view,
#                 ephemeral=True,
#             )

#             # Exit the loop after processing

#     @commands.command(name="rush")
#     @commands.has_permissions(administrator=True)
#     async def admin_rush(self, ctx, army_id: int):
#         """GM Tool: Force an army to arrive by running its task now."""
#         resolve_army_arrival.delay(army_id)
#         await ctx.send(
#             f"⚡ **Divine Wind:** Arrival task for Army {army_id} sent to worker immediately."
#         )

#     @commands.command(name="army")
#     @commands.check(is_in_house_channel)
#     async def army_details(self, ctx):
#         """
#         Detailed military report, paginated for large forces.
#         """
#         discord_id = ctx.author.id
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return

#             stmt = (
#                 select(GamePlayer)
#                 .join(User)
#                 .where(
#                     User.discord_id == discord_id, GamePlayer.game_id == game.game_id
#                 )
#                 .options(selectinload(GamePlayer.house))
#             )
#             player = (await session.execute(stmt)).scalars().first()

#             if not player or not player.claimed_house_id:
#                 return await ctx.send("❌ You do not have a house.")

#             stmt_a = (
#                 select(Army)
#                 .where(
#                     Army.house_id == player.claimed_house_id,
#                     Army.status != "EMBARKED",
#                 )
#                 .order_by(Army.status.desc(), Army.troop_count.desc())
#             )
#             armies = (await session.execute(stmt_a)).scalars().all()

#             if not armies:
#                 return await ctx.send("You have no military forces.")

#             army_chunks = [armies[i : i + 10] for i in range(0, len(armies), 10)]
#             embeds = []

#             now = datetime.datetime.now(datetime.timezone.utc)

#             for chunk in army_chunks:
#                 embed = discord.Embed(
#                     title=f"⚔️ Military Report: House {player.house.name}",
#                     color=discord.Color.red(),
#                 )

#                 for army in chunk:
#                     # --- GHOST ARMY DETECTION (The Fix) ---
#                     # If departure time is in the future, it is hidden cargo. Skip it.
#                     if army.departure_time:
#                         dep_time = army.departure_time
#                         if dep_time.tzinfo is None:
#                             dep_time = dep_time.replace(tzinfo=datetime.timezone.utc)

#                         if dep_time > now:
#                             continue  # <--- THIS HIDES IT COMPLETELY

#                     # 1. Status & ETA Calculation
#                     if army.status in ["MARCHING", "SAILING"]:
#                         if army.arrival_time:
#                             arr_time = army.arrival_time
#                             if arr_time.tzinfo is None:
#                                 arr_time = arr_time.replace(
#                                     tzinfo=datetime.timezone.utc
#                                 )

#                             if arr_time > now:
#                                 remaining = arr_time - now
#                                 hours, rem = divmod(
#                                     int(remaining.total_seconds()), 3600
#                                 )
#                                 minutes, _ = divmod(rem, 60)
#                                 time_str = f"{hours}h {minutes}m"
#                             else:
#                                 time_str = "Arriving..."
#                         else:
#                             time_str = "???"

#                         status_icon = "🦶" if army.status == "MARCHING" else "⛵"
#                         status_str = f"{status_icon} {army.status} (ETA: {time_str})"
#                     else:
#                         status_str = f"🟢 {army.status}"

#                     # 2. Composition Logic
#                     comp_items = []
#                     if army.composition:
#                         for k, v in army.composition.items():
#                             if v <= 0:
#                                 continue
#                             if army.army_type == "SEA" and k.lower() in [
#                                 "ships",
#                                 "ship",
#                                 "galley",
#                                 "galleys",
#                             ]:
#                                 continue
#                             comp_items.append(f"{k.title()[:3]}: {v}")

#                     comp_str = " | ".join(comp_items)
#                     if not comp_str:
#                         comp_str = "-"

#                     loc_str = f"{army.location_x:.0f}, {army.location_y:.0f}"

#                     # 3. Cargo Logic
#                     cargo_str = ""
#                     if army.army_type == "SEA":
#                         count_label = "Ships"
#                         cargo_data = {}
#                         if army.cargo:
#                             if isinstance(army.cargo, dict):
#                                 cargo_data = army.cargo
#                             elif isinstance(army.cargo, str):
#                                 try:
#                                     cargo_data = json.loads(army.cargo)
#                                 except:
#                                     pass

#                         if cargo_data.get("troop_count", 0) > 0:
#                             cargo_str = (
#                                 f"\n📦 **Cargo:** {cargo_data['troop_count']} men"
#                             )
#                     else:
#                         count_label = "Troops"

#                     embed.add_field(
#                         name=f"{army.commander_name} (ID: {army.army_id})",
#                         value=f"**Status:** {status_str}\n**{count_label}:** {army.troop_count}{cargo_str}\n**Comp:** {comp_str}\n**Location:** {loc_str}",
#                         inline=False,
#                     )

#                 embeds.append(embed)

#             from app.ui.paginator import Paginator

#             if embeds:
#                 if len(embeds) == 1:
#                     await ctx.send(embed=embeds[0])
#                 else:
#                     view = Paginator(embeds)
#                     await ctx.send(embed=embeds[0], view=view)

#     @commands.command(name="split")
#     @commands.check(is_in_house_channel)
#     async def split(self, ctx, army_id: int, amount: int, *, new_name: str):
#         """Splits an army. Usage: !split [ID] [Amount] [Name]"""
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             stmt = select(User).where(User.discord_id == ctx.author.id)
#             user = (await session.execute(stmt)).scalars().first()

#             service = WarfareService(session)
#             success, msg = await service.split_army(
#                 game.game_id, user.user_id, army_id, amount, new_name
#             )
#             await ctx.send(msg)

#     @commands.command(name="merge")
#     @commands.check(is_in_house_channel)
#     async def merge(self, ctx, army_id_1: int, army_id_2: int):
#         """Merges two armies. Usage: !merge [ID1] [ID2]"""
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             stmt = select(User).where(User.discord_id == ctx.author.id)
#             user = (await session.execute(stmt)).scalars().first()

#             service = WarfareService(session)
#             success, msg = await service.merge_armies(
#                 game.game_id, user.user_id, army_id_1, army_id_2
#             )
#             await ctx.send(msg)

#     @commands.command(name="form_coalition")
#     @commands.check(is_in_house_channel)
#     async def form_coalition(self, ctx, new_name: str, *army_ids: int):
#         """
#         Merges multiple armies.
#         - If you own all armies, they merge instantly.
#         - If armies are owned by multiple players, a consent proposal is created.
#         Usage: !form_coalition "Name" 101 102 103
#         """
#         if not army_ids:
#             return await ctx.send("❌ You must provide at least two army IDs.")

#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await ctx.send("❌ No active game found.")

#             # Fetch all armies and their owners
#             armies = await ArmyRepo.get_armies_by_ids(session, list(army_ids))
#             if len(armies) != len(army_ids):
#                 return await ctx.send("❌ One or more army IDs are invalid.")

#             # --- NEW LOGIC: Identify all unique owners ---
#             owners_map = {}  # {owner_discord_id: [Army, ...]}
#             for army in armies:
#                 # Find the primary player who owns this army's house
#                 player_owner = await session.scalar(
#                     select(GamePlayer)
#                     .join(User)
#                     .where(
#                         GamePlayer.claimed_house_id == army.house_id,
#                         GamePlayer.is_primary == True,
#                     )
#                     .options(selectinload(GamePlayer.user))
#                 )
#                 if not player_owner or not player_owner.user:
#                     return await ctx.send(
#                         f"❌ Could not find a player for House {army.house.name}."
#                     )

#                 owner_id = player_owner.user.discord_id
#                 if owner_id not in owners_map:
#                     owners_map[owner_id] = []
#                 owners_map[owner_id].append(army)

#             # --- SCENARIO 1: SOLO MERGE ---
#             if len(owners_map) == 1 and ctx.author.id in owners_map:
#                 await ctx.send("🤝 Merging your own units...")
#                 service = WarfareService(session)
#                 # Call the service normally, without bypassing auth
#                 success, msg = await service.form_coalition(
#                     game.game_id, ctx.author.id, new_name, army_ids
#                 )
#                 await ctx.send(msg)

#             # --- SCENARIO 2: MULTI-PLAYER PROPOSAL ---
#             else:
#                 targets_map = {}  # {discord.Member: [Army, ...]}
#                 mentions = []
#                 for owner_id, owned_armies in owners_map.items():
#                     try:
#                         # FIX: Use fetch_member to bypass cache issues
#                         member = await ctx.guild.fetch_member(owner_id)
#                     except discord.NotFound:
#                         return await ctx.send(
#                             f"❌ Player with ID {owner_id} not found in this server."
#                         )
#                     except discord.HTTPException:
#                         return await ctx.send(
#                             f"❌ Discord API error while fetching user {owner_id}."
#                         )

#                     targets_map[member] = owned_armies

#                     # Only mention people who are not the one starting the proposal
#                     if member != ctx.author:
#                         mentions.append(member.mention)

#                 # THIS IS WHERE YOU USE THE CONSENT VIEW
#                 view = CoalitionConsentView(
#                     self.bot, ctx.author, targets_map, game.game_id, new_name, army_ids
#                 )
#                 await ctx.send(
#                     f"A coalition has been proposed! {', '.join(mentions)}",
#                     embed=view.create_embed(),
#                     view=view,
#                 )

#     @commands.command(name="disband")
#     @commands.check(is_in_house_channel)
#     async def disband_coalition(self, ctx, army_id: int):
#         """Disbands a coalition. Usage: !disband [ID]"""
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             stmt = select(User).where(User.discord_id == ctx.author.id)
#             user = (await session.execute(stmt)).scalars().first()

#             service = WarfareService(session)
#             success, msg = await service.disband_coalition(
#                 game.game_id, user.user_id, army_id
#             )
#             await ctx.send(msg)

#     @commands.group(name="worldrule", invoke_without_command=True)
#     @commands.has_permissions(administrator=True)
#     async def worldrule(self, ctx):
#         """Parent command for managing world rules."""
#         await ctx.send("Subcommands: `setbridge`, `setrivers`, `setsea`.")

#     @worldrule.command(name="setbridge")
#     @commands.has_permissions(administrator=True)
#     async def set_bridge(self, ctx, bridge_name: str, status: str):
#         bridge_map = {
#             "twins": "twins_open",
#             "rubyford": "rubyford_open",
#             "bitterbridge": "bitterbridge_open",
#         }
#         rule_name = bridge_map.get(bridge_name.lower())
#         if not rule_name:
#             return await ctx.send("❌ Invalid bridge name.")
#         is_enabled = status.lower() in ["on", "open", "enabled"]
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return
#             msg = await WarfareService(session).set_world_rule(
#                 game.game_id, rule_name, is_enabled
#             )
#             await ctx.send(msg)

#     @worldrule.command(name="setrivers")
#     @commands.has_permissions(administrator=True)
#     async def set_rivers(self, ctx, status: str):
#         is_enabled = status.lower() in ["impassable", "on", "enabled"]
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return
#             msg = await WarfareService(session).set_world_rule(
#                 game.game_id, "rivers_impassable", is_enabled
#             )
#             await ctx.send(msg)

#     @worldrule.command(name="setsea")
#     @commands.has_permissions(administrator=True)
#     async def set_sea(self, ctx, status: str):
#         is_enabled = status.lower() in ["allowed", "on", "enabled"]
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return
#             msg = await WarfareService(session).set_world_rule(
#                 game.game_id, "sea_travel_allowed", is_enabled
#             )
#             await ctx.send(msg)

#     async def handle_interception_notification(self, data):
#         """
#         data = { location, parties: [{house, owner, commander, troops, is_moving}, ...] }
#         """
#         guild = self.bot.get_guild(data["guild_id"])
#         if not guild:
#             return

#         # Notify BOTH parties
#         for party in data["parties"]:
#             # Find their channel
#             chan_name = f"{party['house_name'].lower().replace(' ', '-')}-quarters"
#             channel = discord.utils.get(guild.text_channels, name=chan_name)

#             # Determine Enemy info (it's the other party in the list)
#             enemy = [p for p in data["parties"] if p != party][0]

#             if channel:
#                 embed = discord.Embed(
#                     title="⚔️ Scout Report: Contact Imminent",
#                     description=f"My Lord, outriders report an encounter near **{data['location']}**!",
#                     color=discord.Color.orange(),
#                 )
#                 embed.add_field(
#                     name="Hostile Force", value=enemy["commander"], inline=True
#                 )
#                 enemy_unit_noun = "ships" if enemy.get("army_type") == "SEA" else "men"
#                 embed.add_field(
#                     name="Est. Strength",
#                     value=f"~{enemy['troops']} {enemy_unit_noun}",
#                     inline=True,
#                 )
#                 embed.add_field(
#                     name="Est. Strength", value=f"~{enemy['troops']} men", inline=True
#                 )

#                 status = "Marching" if enemy["is_moving"] else "Encamped"
#                 embed.set_footer(
#                     text=f"Enemy Status: {status} | Please Ping GM to resolve."
#                 )

#                 # Tag the user
#                 if party["owner_id"]:
#                     await channel.send(f"<@{party['owner_id']}>", embed=embed)

#     @commands.command(name="rush_all")
#     @commands.has_permissions(administrator=True)
#     async def rush_all(self, ctx, *, destination: str = None):
#         """
#         GM Tool: Instantly completes marches.
#         Usage: !rush_all Winterfell (Rushes everyone marching to Winterfell)
#         Usage: !rush_all (Rushes EVERY marching army in the game)
#         """
#         count = 0
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return

#             # Base query: Active Game + Marching Status
#             stmt = select(Army).where(
#                 Army.game_id == game.game_id, Army.status.in_(["MARCHING", "SAILING"])
#             )

#             # Filter by destination if provided
#             if destination:
#                 service = WarfareService(session)
#                 loc = await service._get_location_from_db(game.game_id, destination)
#                 if not loc:
#                     await ctx.send(f"❌ Location **{destination}** not found.")
#                     return

#                 # Filter armies heading to these specific coordinates
#                 stmt = stmt.where(
#                     Army.destination_x == loc["x"], Army.destination_y == loc["y"]
#                 )

#             armies = (await session.execute(stmt)).scalars().all()

#             if not armies:
#                 await ctx.send("⚠️ No armies found matching those criteria.")
#                 return

#             # Fire the tasks
#             for army in armies:
#                 resolve_army_arrival.delay(army.army_id)
#                 count += 1

#             target_msg = (
#                 f"to **{destination}**" if destination else "in the **entire world**"
#             )
#             await ctx.send(
#                 f"⚡ **Divine Wind:** Rushed **{count}** armies {target_msg}."
#             )

#     @commands.command(name="embark")
#     @commands.check(is_in_house_channel)
#     async def embark(self, ctx, land_army_id: int, fleet_id: int):
#         """
#         Loads a land army onto a fleet at the same location.
#         Usage: !embark [Land_Army_ID] [Fleet_ID]
#         """
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return

#             stmt = select(User).where(User.discord_id == ctx.author.id)
#             user = (await session.execute(stmt)).scalars().first()
#             if not user:
#                 return

#             service = WarfareService(session)
#             success, msg = await service.embark_army(
#                 game.game_id, user.user_id, land_army_id, fleet_id
#             )
#             await ctx.send(msg)

#     @commands.command(name="disembark")
#     @commands.check(is_in_house_channel)
#     async def disembark(self, ctx, army_id: int):
#         """
#         Unloads troops from a fleet to the current location.
#         Usage: !disembark [Fleet_ID]
#         """
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return

#             stmt = select(User).where(User.discord_id == ctx.author.id)
#             user = (await session.execute(stmt)).scalars().first()
#             if not user:
#                 return

#             service = WarfareService(session)
#             success, msg = await service.disembark_army(
#                 game.game_id, user.user_id, army_id
#             )
#             await ctx.send(msg)

#     @commands.command(name="recruit")
#     @commands.check(
#         recruitment_is_enabled
#     )  # This now correctly checks the manpower_enabled flag
#     @commands.check(is_in_house_channel)
#     async def recruit(self, ctx, fief_name: str, amount: int):
#         """
#         Recruit troops from your manpower pool into a garrison.
#         Usage: !recruit Winterfell 1000
#         """
#         # NO CHANGES NEEDED HERE. The decorators and service handle everything.
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return

#             stmt = select(User).where(User.discord_id == ctx.author.id)
#             user = (await session.execute(stmt)).scalars().first()
#             if not user:
#                 return

#             service = WarfareService(session)
#             success, msg = await service.recruit_troops(
#                 game.game_id, user.user_id, fief_name, amount
#             )
#             await ctx.send(msg)

#     @commands.command(name="sail")
#     @commands.check(is_in_house_channel)
#     async def sail(self, ctx):
#         """Initiates the interactive sail order UI for SEA armies."""
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await ctx.send("❌ No active game.")

#             player = await session.scalar(
#                 select(GamePlayer)
#                 .join(User)
#                 .where(
#                     User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#                 )
#             )
#             if not player or not player.claimed_house_id:
#                 return await ctx.send("❌ You do not have a house to command.")

#             # Fetch only the user's available SEA armies (fleets)
#             available_fleets = (
#                 (
#                     await session.execute(
#                         select(Army).where(
#                             Army.house_id == player.claimed_house_id,
#                             Army.army_type == "SEA",
#                             Army.status.in_(
#                                 ["IDLE", "DOCKED", "GARRISONED", "RETREATING"]
#                             ),  # Or whatever your idle statuses are
#                         )
#                     )
#                 )
#                 .scalars()
#                 .all()
#             )

#             if not available_fleets:
#                 return await ctx.send("You have no fleets available to sail.")

#             view = FleetSelectView(bot=self.bot, fleets=available_fleets)
#             await ctx.send(
#                 "**Step 1: Select a fleet to command.**",
#                 view=view,
#             )

#     @commands.command(name="stop", aliases=["halt"])
#     @commands.check(is_in_house_channel)
#     async def stop(self, ctx, army_id: int):
#         """Stops a moving army or fleet immediately. Usage: !stop [ID]"""
#         async with get_session() as session:
#             # 1. Setup Game & User
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await ctx.send("❌ No active game.")

#             stmt = select(User).where(User.discord_id == ctx.author.id)
#             user = (await session.execute(stmt)).scalars().first()
#             if not user:
#                 return await ctx.send("❌ You are not registered.")

#             # 2. Call Service
#             service = WarfareService(session)
#             success, msg = await service.stop_march(game.game_id, user.user_id, army_id)

#             # 3. Handle "Ghost Army" Cleanup (The Fleet Patch)
#             # If we successfully stopped a FLEET, we must check if there was a
#             # pre-scheduled Land Army waiting for it (Ghost Army) and delete it.
#             if success:
#                 # We re-fetch the army to check if it was a Fleet
#                 army = await ArmyRepo.get_army_by_id(session, army_id)
#                 if army and army.army_type == "SEA":
#                     # Look for any future marches for this house
#                     import datetime

#                     now = datetime.datetime.now(datetime.timezone.utc)
#                     stmt_ghost = select(Army).where(
#                         Army.house_id == army.house_id,
#                         Army.army_type == "LAND",
#                         Army.status == "MARCHING",
#                         Army.departure_time > now,
#                     )
#                     ghosts = (await session.execute(stmt_ghost)).scalars().all()

#                     if ghosts:
#                         for ghost in ghosts:
#                             # Put troops back into the fleet cargo
#                             if not army.cargo:
#                                 army.cargo = {
#                                     "commander": ghost.commander_name,
#                                     "troop_count": ghost.troop_count,
#                                     "composition": ghost.composition,
#                                 }
#                             # Delete the ghost
#                             await session.delete(ghost)

#                         await session.commit()
#                         msg += "\n(⚠️ Cancelled scheduled disembarkation orders)"

#             await ctx.send(msg)

#     @commands.command()
#     async def occupy(self, ctx, army_id: int):
#         """Occupies a Fief if it is undefended."""
#         async with get_session() as session:
#             # 1. Resolve Guild ID -> Game ID
#             # You must fetch the game object first!
#             from app.db.repositories import GameRepo  # Ensure this is imported

#             game = await GameRepo.get_active_game(session, ctx.guild.id)

#             if not game:
#                 await ctx.send("❌ No active game found in this server.")
#                 return

#             service = WarfareService(session)

#             # 2. Pass game.game_id (Small Int), NOT ctx.guild.id (Big Int)
#             success, msg = await service.occupy_fief(
#                 game.game_id, ctx.author.id, army_id
#             )

#             await ctx.send(msg)


# async def setup(bot):
#     await bot.add_cog(WarfareCog(bot))

# UNCOMMMENT EVERYTHING ABOVE THIS 1826


import discord
from discord.ext import commands, tasks
from sqlalchemy import select, or_, func, delete, text
from sqlalchemy.orm import selectinload
import asyncio
import json
import os
import re
import datetime
import redis.asyncio as redis

from app.db.db_manager import get_session
from app.db.models import (
    User,
    Army,
    GamePlayer,
    House,
    PendingInteraction,
    Game,
    Fief,
    Character,
)
from app.db.repositories import GameRepo, ArmyRepo, FiefRepo

from app.services.warfare_service import WarfareService, FOG_OF_WAR_THRESHOLD
from app.tasks.light_tasks import resolve_army_arrival, handle_gate_response
from app.ui.march_view import ArmySelectView, JourneyArmySelectView
from app.ui.paginator import Paginator
from app.ui.sail_view import FleetSelectView
from app.ui.redirect_view import RedirectSelectView
from app.ui.coalition_view import CoalitionConsentView
from app.ui.gate_view import GateActionView
from app.ui.interaction_view import InteractionView
from app.ui.autobattle_view import AutoBattleControlView
from app.ui.gm_march_view import GMMarchArmySelectView
from app.checks import (
    is_in_house_channel,
    recruitment_is_enabled,
)  # Assuming recruitment_is_enabled is correctly defined elsewhere


fief_cache = {}


# --- Custom GM Check (Needs to be defined outside the Cog or as a static method/helper) ---
async def is_gm(ctx):
    """
    Checks if the command author is a registered User and has the is_gm flag.
    Also returns True if the author is the guild owner or has administrator permissions.
    """
    # First, check for Discord-level admin permissions for immediate access
    if ctx.author.guild_permissions.administrator:
        return True

    # If not an admin, check the database flag
    async with get_session() as session:
        user = await session.scalar(
            select(User).where(User.discord_id == ctx.author.id)
        )
        # This now safely handles the case where 'user' is None
        return user and user.is_gm


# --- UTILITY FUNCTIONS ---
def parse_loose_json(text: str) -> dict:
    """
    A robust parser that handles unquoted keys AND values.
    Input:  { from: Winterfell, to: "Moat Cailin", units: {inf: 100} }
    Output: {'from': 'Winterfell', 'to': 'Moat Cailin', 'units': {'inf': 100}}
    """
    text = text.strip()
    # Remove outer braces if present
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]

    # 1. Split by comma, respecting nested braces
    items = []
    buffer = ""
    depth = 0
    for char in text:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1

        if char == "," and depth == 0:
            items.append(buffer.strip())
            buffer = ""
        else:
            buffer += char
    if buffer.strip():
        items.append(buffer.strip())

    result = {}
    for item in items:
        if ":" not in item:
            continue

        # Split into Key and Value
        key, val = item.split(":", 1)
        key = key.strip().strip('"').strip("'")  # Clean key
        val = val.strip()

        # Logic to determine Value Type

        # A. Nested Object (Recursion)
        if val.startswith("{"):
            val = parse_loose_json(val)
        # B. Integer
        elif val.isdigit():
            val = int(val)
        # C. Boolean
        elif val.lower() == "true":
            val = True
        elif val.lower() == "false":
            val = False
        # D. String (Remove quotes if they exist, keep raw text if they don't)
        else:
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]

        result[key] = val

    return result


def calculate_army_size_from_units(units_input) -> int:
    """Calculates total army size from various unit input formats."""
    if isinstance(units_input, int):
        return units_input
    if isinstance(units_input, str) and units_input.isdigit():
        return int(units_input)
    if isinstance(units_input, dict):
        return sum(filter(lambda v: isinstance(v, int), units_input.values()))
    return 1000  # Default fallback if unknown


# --- MAIN COG ---
class WarfareCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis = None
        self.pubsub = None
        asyncio.create_task(self.connect_and_listen())

    # Moved from global to be a method of WarfareCog
    async def get_discord_user_from_house(self, session, game_id, house_id):
        """Helper to find the Discord User associated with a House ID."""
        stmt = (
            select(User)
            .join(GamePlayer)
            .where(
                GamePlayer.game_id == game_id,
                GamePlayer.claimed_house_id == house_id,
                GamePlayer.is_primary == True,
            )
        )
        user_db = (await session.execute(stmt)).scalars().first()
        if user_db:
            return self.bot.get_user(user_db.discord_id)
        return None

    def cog_unload(self):
        self.redis_listener.cancel()
        if self.redis:
            asyncio.create_task(self.redis.close())

    async def connect_and_listen(self):
        """
        Keeps a persistent connection to Redis and restarts the listener on disconnect.
        This is the main connection management loop.
        """
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                print("🟢 Connecting to Redis...")
                self.redis = redis.from_url(self.redis_url, decode_responses=True)
                self.pubsub = self.redis.pubsub()
                await self.pubsub.subscribe("westeros_bot_events")
                print("✅ Redis Listener Subscribed.")
                self.redis_listener.start()
                return

            except redis.exceptions.ConnectionError as e:
                print(f"⚠️ Could not connect to Redis: {e}. Retrying in 10 seconds...")
                if self.redis_listener.is_running():
                    self.redis_listener.stop()
                if self.redis:
                    await self.redis.close()
                self.redis = None
                self.pubsub = None
                await asyncio.sleep(10)

    @tasks.loop(seconds=1.0)
    async def redis_listener(self):
        """
        This task's ONLY job is to listen for messages on the established connection.
        It should NOT create a new connection.
        """
        try:
            async for message in self.pubsub.listen():
                if message and message["type"] == "message":
                    asyncio.create_task(self.process_message(message["data"]))
        except Exception as e:
            print(f"⚠️ Redis listener error: {e}. Attempting to reconnect...")
            self.redis_listener.stop()
            asyncio.create_task(self.connect_and_listen())

    async def process_message(self, raw_data):
        """Processes the message without blocking the listener."""
        try:
            data = json.loads(raw_data)
            if data["type"] == "ARRIVAL":
                await self.handle_arrival_notification(data)
            elif data["type"] == "INTERCEPTION":
                await self.handle_interception_notification(data)
            elif data["type"] in ["PATH_READY", "PATH_FAILED"]:
                await self.handle_path_notification(data)
            elif data["type"] == "BANKRUPTCY_ALERT":
                await self.handle_bankruptcy_notification(data)
            elif data["type"] == "BANNER_REPORT":
                await self.handle_banner_report(data)
            elif data["type"] == "GATE_ALERT":
                await self.handle_gate_alert(data)
            elif data["type"] == "GATE_RESPONSE":
                handle_gate_response.delay(data["army_id"], data["action"])
            elif data["type"] == "PASSAGE_DENIED":
                await self.handle_passage_denied(data)
            elif data["type"] == "PROMPT_INTERACTION":
                await self.handle_prompt_interaction(data)
            elif data["type"] in [
                "INTERACTION_BATTLE",
                "INTERACTION_MEETING",
                "INTERACTION_ENDED",
            ]:
                await self.handle_interaction_resolution(data)
            elif data["type"] == "PROMPT_AUTOBATTLE":
                await self.handle_prompt_autobattle(data)
            elif data["type"] == "BATTLE_REPORT_ROUND":
                await self.handle_battle_round_report(data)
            elif data["type"] == "BATTLE_REPORT_FINAL":
                await self.handle_battle_final_report(data)
            elif data["type"] == "BATTLE_STARTED":
                await self.handle_battle_started(data)
        except Exception as e:
            print(f"❌ Error processing Redis message: {e}")

    async def handle_prompt_autobattle(self, data):
        """
        Receives the prompt from the Celery worker and posts the GM choice UI.
        """
        guild = self.bot.get_guild(data.get("guild_id"))
        if not guild:
            return

        gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
        if not gm_channel:
            print(
                "ERROR: Could not find #gm-alerts channel to post auto-battle prompt."
            )
            return

        embed = discord.Embed(
            title="🛡️ Auto-Battle Pending",
            description="A battle has been triggered by player actions. You have **15 minutes** to intervene before the auto-battle begins.",
            color=discord.Color.dark_red(),
        )
        embed.add_field(
            name="Attacker", value=data.get("attacker_name", "Unknown"), inline=True
        )
        embed.add_field(
            name="Defender", value=data.get("defender_name", "Unknown"), inline=True
        )
        embed.set_footer(text=f"Battle ID: {data.get('battle_id')}")

        view = AutoBattleControlView(
            battle_id=data.get("battle_id"),
            resolver_task_id=data.get("resolver_task_id"),
        )

        gm_role = discord.utils.get(guild.roles, name="Game Master")
        mention = gm_role.mention if gm_role else ""

        try:
            await gm_channel.send(content=f"🚨 {mention}", embed=embed, view=view)
        except Exception as e:
            print(f"Error sending auto-battle prompt to GM channel: {e}")

    async def handle_battle_started(self, data):
        """Posts an announcement that an auto-battle has begun."""
        guild = self.bot.get_guild(data.get("guild_id"))
        if not guild:
            return

        battle_channel = discord.utils.get(guild.text_channels, name="battle-reports")
        if not battle_channel:
            return

        embed = discord.Embed(
            title="⚔️ A Skirmish Has Erupted!",
            description=f"Forces from **House {data['attacker_house']}** under **{data['attacker_name']}** have engaged with the army of **House {data['defender_house']}** led by **{data['defender_name']}**!",
            color=discord.Color.dark_orange(),
        )
        embed.set_footer(
            text=f"Battle ID: {data.get('battle_id')}. Reports will follow."
        )

        try:
            await battle_channel.send(embed=embed)
        except discord.Forbidden:
            print(f"ERROR: Bot lacks permission to post in #{battle_channel.name}")

    async def handle_battle_round_report(self, data):
        """Posts a simplified result of a single auto-battle round with NO casualty fields."""
        guild = self.bot.get_guild(data.get("guild_id"))
        if not guild:
            return

        battle_channel = discord.utils.get(guild.text_channels, name="battle-reports")
        if not battle_channel:
            return

        scores = data.get("scores", {})

        embed = discord.Embed(
            title=f"⚔️ Round {data.get('round_number', '?')} Analysis ({scores.get('attacker',0)} - {scores.get('defender',0)})",
            description=data.get("roll_msg", "Roll details unavailable."),
            color=discord.Color.dark_grey(),
        )
        embed.set_footer(text=f"Battle ID: {data.get('battle_id')}")

        try:
            await battle_channel.send(embed=embed)
        except discord.Forbidden:
            print(f"ERROR: Bot lacks permission to post in #{battle_channel.name}")

    # async def handle_battle_final_report(self, data):
    #     """Posts the final conclusion of an auto-battle."""
    #     print("\n--- DEBUG: handle_battle_final_report START ---")
    #     guild_id = data.get("guild_id")
    #     battle_id = data.get("battle_id")
    #     report_string = data.get("report_string")
    #     print(
    #         f"  - Received final report for Battle ID: {battle_id} in Guild ID: {guild_id}"
    #     )

    #     if not guild_id or not battle_id or not report_string:
    #         print(
    #             "  - CRITICAL ERROR: Payload is missing guild_id, battle_id, or report_string. Aborting."
    #         )
    #         return

    #     guild = self.bot.get_guild(guild_id)
    #     if not guild:
    #         print(
    #             f"  - CRITICAL ERROR: Bot could not find Guild with ID {guild_id}. Aborting."
    #         )
    #         return

    #     print(f"  - Found Guild: '{guild.name}'")

    #     battle_channel = discord.utils.get(guild.text_channels, name="battle-reports")
    #     if not battle_channel:
    #         print(
    #             f"  - CRITICAL ERROR: Could not find #battle-reports channel in '{guild.name}'. Aborting."
    #         )
    #         return

    #     print(f"  - Found Channel: '#{battle_channel.name}'")

    #     embed = discord.Embed(
    #         title=f"🏁 Battle Concluded! (ID: {battle_id})",
    #         description=report_string,
    #         color=discord.Color.green(),
    #     )

    #     try:
    #         await battle_channel.send(embed=embed)
    #         print(f"  - SUCCESS: Posted final report for Battle ID: {battle_id}")
    #     except discord.Forbidden:
    #         print(
    #             f"  - DISCORD ERROR: Bot lacks permission to post final report in #{battle_channel.name}"
    #         )
    #     except Exception as e:
    #         print(
    #             f"  - UNEXPECTED ERROR: An error occurred while posting the final report: {e}"
    #         )

    #     print("--- DEBUG: handle_battle_final_report END ---\n")

    async def handle_battle_final_report(self, data):
        """Posts the final conclusion of an auto-battle."""
        print("\n--- DEBUG: handle_battle_final_report START ---")
        guild_id = data.get("guild_id")
        battle_id = data.get("battle_id")

        # --- FIX STARTS HERE: Handle Tuple vs String ---
        raw_report = data.get("report_string")

        if isinstance(raw_report, (list, tuple)):
            # If it came as ('Text', GuildID), grab just the text (index 0)
            report_string = raw_report[0]
        else:
            # It's already a clean string
            report_string = raw_report
        # --- FIX ENDS HERE ---

        print(
            f"  - Received final report for Battle ID: {battle_id} in Guild ID: {guild_id}"
        )

        if not guild_id or not battle_id or not report_string:
            print(
                "  - CRITICAL ERROR: Payload is missing guild_id, battle_id, or report_string. Aborting."
            )
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            print(
                f"  - CRITICAL ERROR: Bot could not find Guild with ID {guild_id}. Aborting."
            )
            return

        print(f"  - Found Guild: '{guild.name}'")

        battle_channel = discord.utils.get(guild.text_channels, name="battle-reports")
        if not battle_channel:
            print(
                f"  - CRITICAL ERROR: Could not find #battle-reports channel in '{guild.name}'. Aborting."
            )
            return

        print(f"  - Found Channel: '#{battle_channel.name}'")

        embed = discord.Embed(
            title=f"🏁 Battle Concluded! (ID: {battle_id})",
            description=report_string,  # Now guaranteed to be just the text
            color=discord.Color.green(),
        )

        try:
            await battle_channel.send(embed=embed)
            print(f"  - SUCCESS: Posted final report for Battle ID: {battle_id}")
        except discord.Forbidden:
            print(
                f"  - DISCORD ERROR: Bot lacks permission to post final report in #{battle_channel.name}"
            )
        except Exception as e:
            print(
                f"  - UNEXPECTED ERROR: An error occurred while posting the final report: {e}"
            )

        print("--- DEBUG: handle_battle_final_report END ---\n")

    async def handle_passage_denied(self, payload: dict):
        """
        Notifies an attacker that their passage through a gate was denied.
        """
        guild = self.bot.get_guild(payload.get("guild_id"))
        if not guild:
            return

        army_id = payload.get("army_id")
        gate_name = payload.get("gate_name")
        denied_by = payload.get("denied_by")

        async with get_session() as session:
            army = await session.get(Army, army_id, options=[selectinload(Army.house)])
            if not army or not army.house:
                print(
                    f"Could not find army or house for ID {army_id} to send denial notice."
                )
                return

            stmt = (
                select(GamePlayer)
                .join(User)
                .where(
                    GamePlayer.claimed_house_id == army.house_id,
                    GamePlayer.game_id == army.game_id,
                    GamePlayer.is_primary == True,
                )
                .options(selectinload(GamePlayer.user))
            )
            player = (await session.execute(stmt)).scalars().first()

            if not player or not player.user:
                # If NPC army, just log or send to GM-alerts
                gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
                if gm_channel:
                    embed_npc = discord.Embed(
                        title="❌ NPC March Halted: Passage Denied",
                        description=f"NPC army **{army.commander_name}** of House **{army.house.name}** was denied passage at **{gate_name}** by **{denied_by}**.",
                        color=discord.Color.orange(),
                    )
                    await gm_channel.send(embed=embed_npc)
                return

            attacker_house_name = army.house.name.lower().replace(" ", "-")
            channel_name = f"{attacker_house_name}-quarters"
            target_channel = discord.utils.get(guild.text_channels, name=channel_name)

            if not target_channel:
                # If player channel not found, send to GM-alerts instead
                gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
                if gm_channel:
                    embed_gm = discord.Embed(
                        title="❌ Player March Halted: Passage Denied (Channel Missing)",
                        description=f"Player army **{army.commander_name}** of House **{army.house.name}** (<@{player.user.discord_id}>) was denied passage at **{gate_name}** by **{denied_by}**. Player channel '{channel_name}' not found.",
                        color=discord.Color.red(),
                    )
                    await gm_channel.send(embed=embed_gm)
                return

            embed = discord.Embed(
                title="❌ March Halted: Passage Denied",
                description=f"Your army, **{army.commander_name}**, has been denied passage at **{gate_name}** by **{denied_by}**.",
                color=discord.Color.red(),
            )
            embed.set_footer(text="The army is now idle and awaits new orders.")

            await target_channel.send(f"<@{player.user.discord_id}>", embed=embed)
            print(
                f"Successfully notified attacker in #{target_channel.name} about denied passage."
            )

    async def get_player_channel(self, guild: discord.Guild, house: House):
        """
        Helper to find a player's private house channel using a House object.
        This version does NOT access the database directly for player lookup.
        It assumes you have a way to know if this is a player's house.
        """
        # Note: This helper might need `player.user.discord_id` for accurate channel lookup
        # if the channel naming convention is tied to Discord ID.
        # For now, it sticks to house name.
        if not house:
            return None
        channel_name = f"{house.name.lower().replace(' ', '-')}-quarters"
        return discord.utils.get(guild.text_channels, name=channel_name)

    # async def handle_prompt_interaction(self, data):
    #     """
    #     Sends the UI prompt to both players involved in an interaction.
    #     This version is refactored to prevent async DB conflicts.
    #     """
    #     interaction_id = data["interaction_id"]

    #     # --- Phase 1: All Database Operations in One Block ---
    #     async with get_session() as session:
    #         interaction = await session.get(
    #             PendingInteraction,
    #             interaction_id,
    #             options=[
    #                 selectinload(PendingInteraction.army1)
    #                 .selectinload(Army.house)
    #                 .selectinload(House.game),
    #                 selectinload(PendingInteraction.army2).selectinload(Army.house),
    #             ],
    #         )

    #         if not interaction or not interaction.army1 or not interaction.army2:
    #             print(
    #                 f"DEBUG: Could not process prompt for interaction {interaction_id}. Invalid data."
    #             )
    #             return

    #         guild_id = interaction.army1.house.game.guild_id
    #         army1 = interaction.army1
    #         army2 = interaction.army2

    #         guild = self.bot.get_guild(guild_id)
    #         if not guild:
    #             return

    #         # --- Helper to get discord_id for ping ---
    #         async def _get_discord_id_for_army_house(session, army_obj):
    #             player = await session.scalar(
    #                 select(GamePlayer)
    #                 .join(User)
    #                 .where(
    #                     GamePlayer.claimed_house_id == army_obj.house_id,
    #                     GamePlayer.game_id == army_obj.game_id,
    #                     GamePlayer.is_primary == True,
    #                 )
    #                 .options(selectinload(GamePlayer.user))
    #             )
    #             return player.user.discord_id if player and player.user else None

    #         discord_id_1 = await _get_discord_id_for_army_house(session, army1)
    #         discord_id_2 = await _get_discord_id_for_army_house(session, army2)

    #         # --- Prompt Player 1 (The Marcher) ---
    #         army1_channel = await self.get_player_channel(guild, army1.house)
    #         if army1_channel:
    #             embed = discord.Embed(
    #                 title="⚔️ Army Contact Imminent!",
    #                 description="Your forces are on a collision course with another army. You have **one hour** to issue orders before contact is made.",
    #                 color=discord.Color.orange(),
    #             )
    #             embed.set_footer(
    #                 text=f"Your Army: {army1.commander_name} | Opponent: {army2.commander_name}"
    #             )
    #             view1 = InteractionView(
    #                 interaction_id=interaction.id, for_army_id=army1.army_id
    #             )
    #             try:
    #                 content = f"<@{discord_id_1}>" if discord_id_1 else ""
    #                 msg1 = await army1_channel.send(
    #                     content=content, embed=embed, view=view1
    #                 )
    #                 interaction.army1_channel_id = army1_channel.id
    #                 interaction.army1_message_id = msg1.id
    #             except discord.Forbidden:
    #                 print(
    #                     f"ERROR: Bot does not have permission to send messages in #{army1_channel.name}"
    #                 )
    #                 # Notify GM if player channel not accessible
    #                 gm_channel = discord.utils.get(
    #                     guild.text_channels, name="gm-alerts"
    #                 )
    #                 if gm_channel:
    #                     await gm_channel.send(
    #                         f"WARNING: Bot could not send interaction prompt to {army1.house.name}'s channel. Army1: {army1.commander_name} vs Army2: {army2.commander_name}"
    #                     )

    #         # --- Prompt Player 2 (The Target) ---
    #         army2_channel = await self.get_player_channel(guild, army2.house)
    #         if army2_channel:
    #             embed = discord.Embed(
    #                 title="⚔️ Army Contact Imminent!",
    #                 description="Your forces are on a collision course with another army. You have **one hour** to issue orders before contact is made.",
    #                 color=discord.Color.orange(),
    #             )
    #             embed.set_footer(
    #                 text=f"Your Army: {army2.commander_name} | Opponent: {army1.commander_name}"
    #             )
    #             view2 = InteractionView(
    #                 interaction_id=interaction.id, for_army_id=army2.army_id
    #             )
    #             try:
    #                 content = f"<@{discord_id_2}>" if discord_id_2 else ""
    #                 msg2 = await army2_channel.send(
    #                     content=content, embed=embed, view=view2
    #                 )
    #                 interaction.army2_channel_id = army2_channel.id
    #                 interaction.army2_message_id = msg2.id
    #             except discord.Forbidden:
    #                 print(
    #                     f"ERROR: Bot does not have permission to send messages in #{army2_channel.name}"
    #                 )
    #                 # Notify GM if player channel not accessible
    #                 gm_channel = discord.utils.get(
    #                     guild.text_channels, name="gm-alerts"
    #                 )
    #                 if gm_channel:
    #                     await gm_channel.send(
    #                         f"WARNING: Bot could not send interaction prompt to {army2.house.name}'s channel. Army1: {army1.commander_name} vs Army2: {army2.commander_name}"
    #                     )

    #         if interaction.army1_message_id or interaction.army2_message_id:
    #             await session.commit()

    # async def handle_prompt_interaction(self, data):
    #     """
    #     Sends the UI prompt to both players involved in an interaction.
    #     UPDATED: Routes NPC/GM armies to #gm-alerts instead of failing.
    #     """
    #     interaction_id = data["interaction_id"]

    #     async with get_session() as session:
    #         # 1. Fetch Interaction & Related Data
    #         interaction = await session.get(
    #             PendingInteraction,
    #             interaction_id,
    #             options=[
    #                 selectinload(PendingInteraction.army1)
    #                 .selectinload(Army.house)
    #                 .selectinload(House.game),
    #                 selectinload(PendingInteraction.army2).selectinload(Army.house),
    #             ],
    #         )

    #         if not interaction or not interaction.army1 or not interaction.army2:
    #             print(f"DEBUG: Invalid data for interaction {interaction_id}.")
    #             return

    #         army1 = interaction.army1
    #         army2 = interaction.army2
    #         guild_id = army1.house.game.guild_id

    #         guild = self.bot.get_guild(guild_id)
    #         if not guild:
    #             return

    #         # Lookup GM channel once for use in fallbacks/NPCs
    #         gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")

    #         # --- Helper: Get Discord ID ---
    #         async def _get_discord_id_for_army_house(session, army_obj):
    #             player = await session.scalar(
    #                 select(GamePlayer)
    #                 .join(User)
    #                 .where(
    #                     GamePlayer.claimed_house_id == army_obj.house_id,
    #                     GamePlayer.game_id == army_obj.game_id,
    #                     GamePlayer.is_primary == True,
    #                 )
    #                 .options(selectinload(GamePlayer.user))
    #             )
    #             return player.user.discord_id if player and player.user else None

    #         discord_id_1 = await _get_discord_id_for_army_house(session, army1)
    #         discord_id_2 = await _get_discord_id_for_army_house(session, army2)

    #         # ====================================================
    #         #      SIDE 1: The Active Mover (Army 1)
    #         # ====================================================
    #         if discord_id_1:
    #             # --- CASE: HUMAN PLAYER ---
    #             army1_channel = await self.get_player_channel(guild, army1.house)
    #             if army1_channel:
    #                 embed = discord.Embed(
    #                     title="⚔️ Army Contact Imminent!",
    #                     description=f"Your **{army1.commander_name}** has intercepted **{army2.commander_name}** ({army2.house.name}).\nYou have **one hour** to issue orders.",
    #                     color=discord.Color.orange(),
    #                 )
    #                 view1 = InteractionView(
    #                     interaction_id=interaction.id, for_army_id=army1.army_id
    #                 )
    #                 try:
    #                     msg1 = await army1_channel.send(
    #                         content=f"<@{discord_id_1}>", embed=embed, view=view1
    #                     )
    #                     interaction.army1_channel_id = army1_channel.id
    #                     interaction.army1_message_id = msg1.id
    #                 except discord.Forbidden:
    #                     if gm_channel:
    #                         await gm_channel.send(
    #                             f"⚠️ Bot lacks permissions for {army1_channel.mention}."
    #                         )
    #         else:
    #             # --- CASE: NPC / GM ARMY ---
    #             if gm_channel:
    #                 embed = discord.Embed(
    #                     title="🤖 NPC Interaction (Active)",
    #                     description=f"**NPC House {army1.house.name}** ({army1.commander_name}) has intercepted **{army2.commander_name}**.",
    #                     color=discord.Color.blue(),
    #                 )
    #                 msg1 = await gm_channel.send(embed=embed)
    #                 # We can store the GM message ID if we want, or leave it null
    #                 interaction.army1_channel_id = gm_channel.id
    #                 interaction.army1_message_id = msg1.id

    #         # ====================================================
    #         #      SIDE 2: The Target / Defender (Army 2)
    #         # ====================================================
    #         if discord_id_2:
    #             # --- CASE: HUMAN PLAYER ---
    #             army2_channel = await self.get_player_channel(guild, army2.house)
    #             if army2_channel:
    #                 embed = discord.Embed(
    #                     title="🛡️ Army Contact Imminent!",
    #                     description=f"Your **{army2.commander_name}** has been intercepted by **{army1.commander_name}** ({army1.house.name}).\nYou have **one hour** to issue orders.",
    #                     color=discord.Color.red(),
    #                 )
    #                 view2 = InteractionView(
    #                     interaction_id=interaction.id, for_army_id=army2.army_id
    #                 )
    #                 try:
    #                     msg2 = await army2_channel.send(
    #                         content=f"<@{discord_id_2}>", embed=embed, view=view2
    #                     )
    #                     interaction.army2_channel_id = army2_channel.id
    #                     interaction.army2_message_id = msg2.id
    #                 except discord.Forbidden:
    #                     if gm_channel:
    #                         await gm_channel.send(
    #                             f"⚠️ Bot lacks permissions for {army2_channel.mention}."
    #                         )
    #         else:
    #             # --- CASE: NPC / GM ARMY ---
    #             if gm_channel:
    #                 embed = discord.Embed(
    #                     title="⚠️ NPC Intercepted (Passive)",
    #                     description=f"**NPC House {army2.house.name}** ({army2.commander_name}) is being engaged by **{army1.commander_name}** (House {army1.house.name}).",
    #                     color=discord.Color.orange(),
    #                 )
    #                 msg2 = await gm_channel.send(embed=embed)
    #                 interaction.army2_channel_id = gm_channel.id
    #                 interaction.army2_message_id = msg2.id

    #         # Save the message IDs so the InteractionView can disable them later if needed
    #         if interaction.army1_message_id or interaction.army2_message_id:
    #             await session.commit()

    async def handle_prompt_interaction(self, data):
        """
        Sends the UI prompt. Now sends Interactive Views to GM-Alerts for NPCs.
        """
        interaction_id = data["interaction_id"]

        async with get_session() as session:
            interaction = await session.get(
                PendingInteraction,
                interaction_id,
                options=[
                    selectinload(PendingInteraction.army1)
                    .selectinload(Army.house)
                    .selectinload(House.game),
                    selectinload(PendingInteraction.army2).selectinload(Army.house),
                ],
            )

            if not interaction:
                return

            army1 = interaction.army1
            army2 = interaction.army2
            guild = self.bot.get_guild(army1.house.game.guild_id)
            if not guild:
                return

            gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")

            # --- Helper: Get Discord ID ---
            async def _get_discord_id_for_army_house(session, army_obj):
                player = await session.scalar(
                    select(GamePlayer)
                    .join(User)
                    .where(
                        GamePlayer.claimed_house_id == army_obj.house_id,
                        GamePlayer.game_id == army_obj.game_id,
                        GamePlayer.is_primary == True,
                    )
                    .options(selectinload(GamePlayer.user))
                )
                return player.user.discord_id if player and player.user else None

            discord_id_1 = await _get_discord_id_for_army_house(session, army1)
            discord_id_2 = await _get_discord_id_for_army_house(session, army2)

            # ====================================================
            #      SIDE 1: Active Mover (Army 1)
            # ====================================================
            # Create View for Army 1
            view1 = InteractionView(
                interaction_id=interaction.id, for_army_id=army1.army_id
            )

            if discord_id_1:
                # PLAYER LOGIC (Existing)
                army1_channel = await self.get_player_channel(guild, army1.house)
                if army1_channel:
                    embed = discord.Embed(
                        title="⚔️ Army Contact Imminent!",
                        description=f"Your **{army1.commander_name}** has intercepted **{army2.commander_name}** ({army2.house.name}).",
                        color=discord.Color.orange(),
                    )
                    try:
                        msg1 = await army1_channel.send(
                            content=f"<@{discord_id_1}>", embed=embed, view=view1
                        )
                        interaction.army1_channel_id = army1_channel.id
                        interaction.army1_message_id = msg1.id
                    except:
                        pass
            else:
                # NPC/GM LOGIC (Fixed)
                if gm_channel:
                    embed = discord.Embed(
                        title="🤖 NPC Interaction (Active)",
                        description=f"**NPC {army1.commander_name}** ({army1.house.name}) has intercepted {army2.commander_name}.\n**GM Action Required:** Choose stance for NPC.",
                        color=discord.Color.blue(),
                    )
                    # SEND VIEW TO GM
                    msg1 = await gm_channel.send(embed=embed, view=view1)
                    interaction.army1_channel_id = gm_channel.id
                    interaction.army1_message_id = msg1.id

            # ====================================================
            #      SIDE 2: Passive Defender (Army 2)
            # ====================================================
            # Create View for Army 2
            view2 = InteractionView(
                interaction_id=interaction.id, for_army_id=army2.army_id
            )

            if discord_id_2:
                # PLAYER LOGIC
                army2_channel = await self.get_player_channel(guild, army2.house)
                if army2_channel:
                    embed = discord.Embed(
                        title="🛡️ Army Contact Imminent!",
                        description=f"Your **{army2.commander_name}** has been intercepted by **{army1.commander_name}** ({army1.house.name}).",
                        color=discord.Color.red(),
                    )
                    try:
                        msg2 = await army2_channel.send(
                            content=f"<@{discord_id_2}>", embed=embed, view=view2
                        )
                        interaction.army2_channel_id = army2_channel.id
                        interaction.army2_message_id = msg2.id
                    except:
                        pass
            else:
                # NPC/GM LOGIC (Fixed)
                if gm_channel:
                    embed = discord.Embed(
                        title="⚠️ NPC Intercepted (Passive)",
                        description=f"**NPC {army2.commander_name}** ({army2.house.name}) is being engaged by {army1.commander_name}.\n**GM Action Required:** Choose stance for NPC.",
                        color=discord.Color.orange(),
                    )
                    # SEND VIEW TO GM
                    msg2 = await gm_channel.send(embed=embed, view=view2)
                    interaction.army2_channel_id = gm_channel.id
                    interaction.army2_message_id = msg2.id

            await session.commit()

    # async def handle_interaction_resolution(self, data):
    #     """
    #     Updates the player UI and executes consequences after an interaction is resolved.
    #     This version uses the reliable fetch_member to guarantee players are found.
    #     """
    #     interaction_id = data.get("interaction_id")
    #     outcome = data.get("type")
    #     if not interaction_id or not outcome:
    #         return

    #     async with get_session() as session:
    #         interaction = await session.get(
    #             PendingInteraction,
    #             interaction_id,
    #             options=[
    #                 selectinload(PendingInteraction.army1)
    #                 .selectinload(Army.house)
    #                 .selectinload(House.game),
    #                 selectinload(PendingInteraction.army2).selectinload(Army.house),
    #             ],
    #         )
    #         if not interaction:
    #             return

    #         async def get_player_for_house(house_id: int):
    #             stmt = (
    #                 select(User)
    #                 .join(GamePlayer, User.user_id == GamePlayer.user_id)
    #                 .where(
    #                     GamePlayer.claimed_house_id == house_id,
    #                     GamePlayer.is_primary == True,
    #                 )
    #             )
    #             user = (await session.execute(stmt)).scalar_one_or_none()
    #             return user

    #         user1 = await get_player_for_house(interaction.army1.house_id)
    #         user2 = await get_player_for_house(interaction.army2.house_id)

    #         guild = self.bot.get_guild(interaction.army1.house.game.guild_id)
    #         if not guild:
    #             return

    #         member1, member2 = None, None
    #         try:
    #             if user1 and user1.discord_id:
    #                 member1 = await guild.fetch_member(user1.discord_id)
    #             if user2 and user2.discord_id:
    #                 member2 = await guild.fetch_member(user2.discord_id)
    #         except discord.NotFound:
    #             print(
    #                 f"ERROR: A player's Discord ID from the database was not found in the Discord server for interaction {interaction_id}."
    #             )
    #             return
    #         except Exception as e:
    #             print(f"An unexpected Discord API error occurred: {e}")
    #             return

    #         if outcome == "INTERACTION_MEETING":
    #             if not member1 or not member2:
    #                 print(
    #                     f"ERROR: Cannot create meeting for interaction {interaction_id}. One or both players not found."
    #                 )
    #                 return

    #             diplomacy_cog = self.bot.get_cog("DiplomacyCog")
    #             if not diplomacy_cog:
    #                 print(
    #                     "ERROR: DiplomacyCog not found. Cannot create meeting channel."
    #                 )
    #                 return

    #             location_string = f"The field near ({int(interaction.location_x)}, {int(interaction.location_y)})"
    #             meeting_channel = await diplomacy_cog._create_meeting_channel(
    #                 guild=guild,
    #                 member1=member1,
    #                 member2=member2,
    #                 location_str=location_string,
    #                 name1=interaction.army1.house.name,
    #                 name2=interaction.army2.house.name,
    #             )

    #             if meeting_channel:
    #                 print(
    #                     f"Successfully created meeting channel: {meeting_channel.name}"
    #                 )

    #         elif outcome == "INTERACTION_BATTLE":
    #             print("BATTLE outcome detected. Auto-battle logic will go here.")

    #         elif outcome == "INTERACTION_ENDED":
    #             print("MARCH ON outcome detected. No action taken.")

    async def handle_interaction_resolution(self, data):
        """
        Updates the player UI and executes consequences after an interaction is resolved.
        UPDATED: Sends feedback for 'MARCH_ON' outcome.
        """
        interaction_id = data.get("interaction_id")
        outcome = data.get("type")
        if not interaction_id or not outcome:
            return

        async with get_session() as session:
            # Load interaction with full relationship data
            interaction = await session.get(
                PendingInteraction,
                interaction_id,
                options=[
                    selectinload(PendingInteraction.army1)
                    .selectinload(Army.house)
                    .selectinload(House.game),
                    selectinload(PendingInteraction.army2).selectinload(Army.house),
                ],
            )
            if not interaction:
                return

            guild = self.bot.get_guild(interaction.army1.house.game.guild_id)
            if not guild:
                return

            # --- Outcome: MEETING ---
            if outcome == "INTERACTION_MEETING":
                # Helper to fetch Discord Member objects
                async def get_member_for_house(house_id):
                    stmt = (
                        select(User)
                        .join(GamePlayer, User.user_id == GamePlayer.user_id)
                        .where(
                            GamePlayer.claimed_house_id == house_id,
                            GamePlayer.is_primary == True,
                        )
                    )
                    user = (await session.execute(stmt)).scalar_one_or_none()
                    if user and user.discord_id:
                        try:
                            return await guild.fetch_member(user.discord_id)
                        except:
                            return None
                    return None

                member1 = await get_member_for_house(interaction.army1.house_id)
                member2 = await get_member_for_house(interaction.army2.house_id)

                if not member1 or not member2:
                    # If one is missing (e.g. NPC), we can't make a player meeting channel
                    gm_channel = discord.utils.get(
                        guild.text_channels, name="gm-alerts"
                    )
                    if gm_channel:
                        await gm_channel.send(
                            f"⚠️ **Meeting Error:** Interaction {interaction_id} resolved to MEETING, but one party is an NPC or missing."
                        )
                    return

                diplomacy_cog = self.bot.get_cog("DiplomacyCog")
                if diplomacy_cog:
                    location_string = f"({int(interaction.location_x)}, {int(interaction.location_y)})"
                    await diplomacy_cog._create_meeting_channel(
                        guild=guild,
                        member1=member1,
                        member2=member2,
                        location_str=location_string,
                        name1=interaction.army1.house.name,
                        name2=interaction.army2.house.name,
                    )

            # --- Outcome: BATTLE ---
            elif outcome == "INTERACTION_BATTLE":
                # The Task `initiate_auto_battle` handles the logic and notifications from here.
                # Just logging it for debug.
                print(f"DEBUG: Interaction {interaction_id} escalating to Battle.")

            # --- Outcome: MARCH ON ---
            elif outcome == "INTERACTION_ENDED":
                # Send feedback to the channels where the prompt was originally sent
                embed = discord.Embed(
                    title="🚩 Interaction Resolved: March Resumed",
                    description="Both parties have chosen to ignore the contact (or orders timed out). The armies continue their march.",
                    color=discord.Color.light_grey(),
                )

                # Retrieve the channel IDs saved during the prompt phase
                channels_to_notify = []
                if interaction.army1_channel_id:
                    ch = guild.get_channel(interaction.army1_channel_id)
                    if ch:
                        channels_to_notify.append(ch)

                if interaction.army2_channel_id:
                    ch = guild.get_channel(interaction.army2_channel_id)
                    if ch:
                        channels_to_notify.append(ch)

                # Deduplicate channels (in case GM got both alerts in same channel)
                channels_to_notify = list(set(channels_to_notify))

                for ch in channels_to_notify:
                    try:
                        await ch.send(embed=embed)
                    except discord.Forbidden:
                        pass

    # @commands.Cog.listener()
    # async def on_interaction(self, interaction: discord.Interaction):
    #     """Listener for all button clicks, including our new interaction view."""
    #     custom_id = interaction.data.get("custom_id")
    #     if not custom_id or not custom_id.startswith("interaction_"):
    #         return

    #     try:
    #         _, choice, interaction_id_str, army_id_str = custom_id.split("_")
    #         interaction_id = int(interaction_id_str)
    #         army_id = int(army_id_str)
    #     except ValueError:
    #         await interaction.response.send_message(
    #             "❌ Invalid button ID.", ephemeral=True
    #         )
    #         return

    #     await interaction.response.defer()

    #     async with get_session() as session:
    #         pending_interaction = await session.get(
    #             PendingInteraction,
    #             interaction_id,
    #             options=[selectinload(PendingInteraction.army1)],
    #         )

    #         if not pending_interaction or pending_interaction.status != "PENDING":
    #             await interaction.followup.send(
    #                 "This interaction has already been resolved or has expired.",
    #                 ephemeral=True,
    #             )
    #             return

    #         if army_id == pending_interaction.army1_id:
    #             pending_interaction.army1_choice = choice
    #         elif army_id == pending_interaction.army2_id:
    #             pending_interaction.army2_choice = choice
    #         else:
    #             await interaction.followup.send(
    #                 "Error: You do not command this army.", ephemeral=True
    #             )
    #             return

    #         await session.commit()

    #         await interaction.followup.send(
    #             f"✅ Your choice '{choice.replace('_', ' ')}' has been registered.",
    #             ephemeral=True,
    #         )

    #         original_view = InteractionView(interaction_id, army_id)
    #         await original_view.disable_all_buttons()
    #         await interaction.edit_original_response(view=original_view)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """
        Listener for interaction buttons.
        UPDATED: Allows GMs/Admins to control NPC armies.
        """
        custom_id = interaction.data.get("custom_id")
        if not custom_id or not custom_id.startswith("interaction_"):
            return

        try:
            # Format: interaction_[CHOICE]_[INTERACTION_ID]_[ARMY_ID]
            _, choice, interaction_id_str, army_id_str = custom_id.split("_")
            interaction_id = int(interaction_id_str)
            army_id = int(army_id_str)
        except ValueError:
            return

        await interaction.response.defer()

        async with get_session() as session:
            pending_interaction = await session.get(
                PendingInteraction,
                interaction_id,
                options=[
                    selectinload(PendingInteraction.army1),
                    selectinload(PendingInteraction.army2),
                ],
            )

            if not pending_interaction or pending_interaction.status != "PENDING":
                await interaction.followup.send(
                    "This interaction has expired or been resolved.", ephemeral=True
                )
                return

            # --- AUTHORIZATION LOGIC ---
            is_authorized = False

            # 1. Identify which army this button is for
            target_army = None
            if army_id == pending_interaction.army1_id:
                target_army = pending_interaction.army1
            elif army_id == pending_interaction.army2_id:
                target_army = pending_interaction.army2

            if target_army:
                # 2. Check if User is the Owner
                stmt = (
                    select(User)
                    .join(GamePlayer)
                    .where(
                        GamePlayer.claimed_house_id == target_army.house_id,
                        GamePlayer.game_id == target_army.game_id,
                        GamePlayer.is_primary == True,
                    )
                )
                owner = (await session.execute(stmt)).scalars().first()

                if owner and owner.discord_id == interaction.user.id:
                    is_authorized = True

                # 3. Check if User is GM (Fallback for NPCs or Override)
                # We check Discord permissions for speed, or you can use your is_gm DB check
                elif interaction.user.guild_permissions.administrator:
                    is_authorized = True

            if not is_authorized:
                await interaction.followup.send(
                    "❌ You do not have authority to command this army.", ephemeral=True
                )
                return

            # --- EXECUTE CHOICE ---
            if army_id == pending_interaction.army1_id:
                pending_interaction.army1_choice = choice
            elif army_id == pending_interaction.army2_id:
                pending_interaction.army2_choice = choice

            await session.commit()

            # Feedback
            clean_choice = (
                choice.replace("MARCH_ON", "CONTINUE MARCH").replace("_", " ").title()
            )
            await interaction.followup.send(
                f"✅ Orders confirmed: **{clean_choice}**.", ephemeral=True
            )

            # Disable buttons on the view to prevent double-clicking
            original_view = InteractionView(interaction_id, army_id)
            await original_view.disable_all_buttons()
            try:
                await interaction.edit_original_response(view=original_view)
            except:
                pass

    async def handle_gate_alert(self, payload: dict):
        """
        Processes a GATE_ALERT event and attaches interactive buttons.
        """
        guild = self.bot.get_guild(payload.get("guild_id"))
        if not guild:
            print(f"Could not find guild with ID: {payload.get('guild_id')}")
            return

        defender_info = payload.get(
            "defender", {}
        )  # Renamed to avoid conflict with defender in DB access
        marcher_info = payload.get(
            "marcher", {}
        )  # Renamed to avoid conflict with marcher in DB access
        attacking_army_id = payload.get("attacking_army_id")
        if not attacking_army_id:
            print(
                "CRITICAL: Gate alert received without an 'attacking_army_id'. Cannot create buttons."
            )
            return

        target_channel = None

        if defender_info.get("is_npc"):
            target_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
        else:
            house_name = defender_info.get("house_name", "").lower().replace(" ", "-")
            expected_channel_name = f"{house_name}-quarters"
            target_channel = discord.utils.get(
                guild.text_channels, name=expected_channel_name
            )
            if not target_channel:  # Fallback for player channel not found
                target_channel = discord.utils.get(
                    guild.text_channels, name="gm-alerts"
                )

        if not target_channel:
            print(
                f"CRITICAL: Could not find any channel to post alert in for guild {guild.name}."
            )
            return

        embed = discord.Embed(
            title=f"⚔️ Gate Alert: {payload.get('gate_name')}",
            description=f"An army approaches a strategic chokepoint you control!",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Attacking Army",
            value=f"**{marcher_info.get('commander')}** of House **{marcher_info.get('house_name')}**",
            inline=False,
        )
        embed.add_field(
            name="Troop Count",
            value=f"{marcher_info.get('troops', 'N/A')}",
            inline=True,
        )
        embed.set_footer(text="A decision is required: Grant or Deny Passage.")

        ping_message = ""
        defender_discord_id = defender_info.get("discord_id")
        if not defender_info.get("is_npc") and defender_discord_id:
            ping_message = f"<@{defender_discord_id}>"

        view = GateActionView(
            self.bot, guild.id, attacking_army_id, defender_discord_id
        )

        await target_channel.send(ping_message, embed=embed, view=view)
        print(f"Successfully sent interactive gate alert to #{target_channel.name}")

    async def handle_bankruptcy_notification(self, data):
        """
        Alerts GMs about houses running out of gold.
        data = { guild_id, data: [{name, debt, troops}, ...] }
        """
        guild = self.bot.get_guild(data["guild_id"])
        if not guild:
            return

        channel = discord.utils.get(guild.text_channels, name="gm-alerts")
        if not channel:
            return

        embed = discord.Embed(
            title="📉 Logistics Report: Bankruptcy",
            description="The following houses cannot pay their daily army upkeep.",
            color=discord.Color.dark_red(),
        )

        for entry in data["data"]:
            embed.add_field(
                name=f"House {entry['name']}",
                value=f"**Debt:** {entry['debt']} Gold\n**At Risk:** {entry['troops']} Troops",
                inline=False,
            )

        embed.set_footer(text="Use `!punish [House] [Percent]` to simulate desertion.")
        await channel.send(embed=embed)

    async def handle_banner_report(self, data):
        """
        Sends the Banner Report to the Liege's private channel.
        """
        guild = self.bot.get_guild(data["guild_id"])
        if not guild:
            return

        # Try to find the Liege's Private Channel
        chan_name = f"{data['liege_house_name'].lower().replace(' ', '-')}-quarters"
        channel = discord.utils.get(guild.text_channels, name=chan_name)

        ping_content = ""
        if data["owner_id"]:  # Check if there's a player owner to ping
            ping_content = f"<@{data['owner_id']}>"

        if (
            not channel
        ):  # Fallback to GM channel if player channel not found (e.g. player left)
            channel = discord.utils.get(guild.text_channels, name="gm-alerts")
            if not channel:
                print(
                    f"❌ Could not find channel {chan_name} or gm-alerts to post banner report."
                )
                return
            # If sending to GM channel as fallback, adjust content
            if ping_content:
                ping_content = (
                    f"⚠️ Player channel missing for <@{data['owner_id']}>, sending banner report here. "
                    + ping_content
                )

        embed = discord.Embed(title="🦅 Banner Call Report", color=discord.Color.blue())

        report_text = "\n".join(data["report_lines"])
        if len(report_text) > 3000:
            report_text = report_text[:3000] + "...(truncated)"

        embed.description = report_text
        unit_noun = "ships" if data.get("call_type") == "SEA" else "men"
        embed.add_field(
            name="Total Raised",
            value=f"**{data['total_raised']}** {unit_noun}",
            inline=True,
        )
        embed.add_field(
            name="Full Assembly", value=f"**{data['max_duration']}**", inline=True
        )

        await channel.send(content=ping_content, embed=embed)

    async def handle_arrival_notification(self, data):
        """
        Sends arrival embeds.
        """
        guild_id = data.get("guild_id")
        if not guild_id:
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        unit_noun = "men"
        if data.get("unit_type") == "SEA":
            unit_noun = "ships"

        # --- FIX: Robust Game ID retrieval ---
        # If game_id is missing from payload, we try to fetch active game for the guild
        game_id = data.get("game_id")
        owner_discord_id = None

        async with get_session() as session:
            if not game_id:
                game = await GameRepo.get_active_game(session, guild_id)
                if game:
                    game_id = game.game_id

            if game_id:
                # Determine if the army belongs to a primary player
                player_owner = await session.scalar(
                    select(GamePlayer)
                    .where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.claimed_house_id == data["house_id"],
                        GamePlayer.is_primary == True,
                    )
                    .options(selectinload(GamePlayer.user))
                )
                owner_discord_id = (
                    player_owner.user.discord_id
                    if player_owner and player_owner.user
                    else None
                )

        # Determine channels
        house_name_clean = data["house_name"].lower().replace(" ", "-")
        chan_name_private = f"{house_name_clean}-quarters"
        private_channel = discord.utils.get(guild.text_channels, name=chan_name_private)

        # 1. Private/GM Notification
        if owner_discord_id and private_channel:  # Player-owned army, channel found
            embed_private = discord.Embed(
                title="📍 Arrival Report",
                description=f"**{data['commander']}** ({data['troops']} {unit_noun}) has arrived at **{data['location']}**.",
                color=discord.Color.green(),
            )
            try:
                await private_channel.send(
                    f"<@{owner_discord_id}>", embed=embed_private
                )
            except:
                pass
        else:  # NPC army OR player army with missing channel
            gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
            if gm_channel:
                embed_gm = discord.Embed(
                    title="📍 Arrival Report (NPC/Unclaimed)",
                    description=f"**{data['commander']}** of House **{data['house_name']}** ({data['troops']} {unit_noun}) has arrived at **{data['location']}**.",
                    color=discord.Color.blue(),
                )
                await gm_channel.send(embed=embed_gm)

        # 2. Public Notification
        if data["troops"] >= FOG_OF_WAR_THRESHOLD:
            public_channel = discord.utils.get(
                guild.text_channels, name="army-movements"
            ) or discord.utils.get(guild.text_channels, name="general-movements")

            if public_channel:
                house_role = discord.utils.get(guild.roles, name=data["house_name"])
                mention = (
                    house_role.mention
                    if house_role
                    else f"**House {data['house_name']}**"
                )

                public_msg = f"✅ The forces of {mention} under the command of **{data['commander']}** ({data['troops']} {unit_noun}) have arrived at **{data['location']}**."
                await public_channel.send(public_msg)

    async def handle_path_notification(self, data):
        guild = self.bot.get_guild(data["guild_id"])
        channel = guild.get_channel(data["channel_id"]) if guild else None
        user = guild.get_member(data["user_id"]) if guild else None

        if not channel or not user:
            return

        if data["type"] == "PATH_FAILED":
            await channel.send(
                f"{user.mention}, your journey plan failed: {data['reason']}"
            )
            return

        try:
            # Need to explicitly check if data['image_path'] is a BytesIO object or a file path string
            # If it's BytesIO, you should pass it directly. If it's a string, ensure os.path.exists
            # The WarfareService._generate_path_image returns BytesIO
            if hasattr(
                data["image"], "read"
            ):  # Check if it's a file-like object (BytesIO)
                image_file = discord.File(data["image"], filename="journey.png")
            elif os.path.exists(data["image_path"]):
                image_file = discord.File(data["image_path"], filename="journey.png")
            else:
                await channel.send(
                    f"{user.mention}, map generated but file not found on server."
                )
                return

            embed = discord.Embed(
                title=f"Journey Plan: {data['origin']} to {data['destination']}",
                description=f"Mode: **{data['mode']}**",
                color=discord.Color.blue(),
            )
            embed.add_field(name="Est. Travel Time", value=data["time"], inline=True)
            embed.add_field(
                name="Distance", value=f"~{data['distance']} miles", inline=True
            )
            embed.set_image(url="attachment://journey.png")

            await channel.send(content=f"{user.mention}", file=image_file, embed=embed)

            # If the image was a temporary file, delete it
            if not hasattr(data["image"], "read") and os.path.exists(
                data["image_path"]
            ):
                os.remove(data["image_path"])

        except Exception as e:
            await channel.send(f"❌ Error sending map: {e}")

    @redis_listener.before_loop
    async def before_listener(self):
        await self.bot.wait_until_ready()

    @commands.command(name="fiefs", aliases=["locations", "places"])
    @commands.check(is_in_house_channel)
    async def list_all_fiefs(self, ctx):
        """Displays a complete, paginated list of all known fiefs."""
        async with ctx.typing():
            async with get_session() as session:
                game = await GameRepo.get_active_game(session, ctx.guild.id)
                if not game:
                    return await ctx.send("❌ No active game.")

                all_fiefs_raw = await FiefRepo.get_all_fief_names(session, game.game_id)
                all_fiefs = sorted(list(set(all_fiefs_raw)))

                if not all_fiefs:
                    return await ctx.send(
                        "❌ No fiefs have been defined for this game."
                    )

                CHUNK_SIZE = 25
                fief_chunks = [
                    all_fiefs[i : i + CHUNK_SIZE]
                    for i in range(0, len(all_fiefs), CHUNK_SIZE)
                ]

                embeds = []
                total_pages = len(fief_chunks)

                for i, chunk in enumerate(fief_chunks):
                    description = "```\n" + "\n".join(chunk) + "\n```"

                    embed = discord.Embed(
                        title="📜 List of Known Fiefs",
                        description=description,
                        color=discord.Color.blurple(),
                    )
                    embed.set_footer(text=f"Page {i + 1} of {total_pages}")
                    embeds.append(embed)

                if not embeds:
                    return await ctx.send("Could not generate the fief list.")

                if len(embeds) == 1:
                    await ctx.send(embed=embeds[0])
                else:
                    view = Paginator(embeds)
                    await ctx.send(embed=embeds[0], view=view)

    @commands.command(name="journey", aliases=["plan"])
    @commands.check(is_in_house_channel)
    async def journey(self, ctx):
        """Initiates the interactive journey planning UI for any army or fleet."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )
            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not have a house to command.")

            available_units = (
                (
                    await session.execute(
                        select(Army).where(Army.house_id == player.claimed_house_id)
                    )
                )
                .scalars()
                .all()
            )

            if not available_units:
                return await ctx.send(
                    "You have no units to use as a starting point for a plan."
                )

            view = JourneyArmySelectView(bot=self.bot, armies=available_units)
            await ctx.send(
                "**Step 1: Select a starting unit for your journey plan.**",
                view=view,
                ephemeral=True,
            )

    # --- PLAYER COMMANDS ---
    @commands.command(name="march")
    @commands.check(is_in_house_channel)
    async def march(self, ctx):
        """Initiates the interactive march order UI for LAND armies."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )

            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not have a house to command.")

            available_armies = (
                (
                    await session.execute(
                        select(Army).where(
                            Army.house_id == player.claimed_house_id,
                            Army.army_type == "LAND",
                            Army.status.in_(["IDLE", "GARRISONED", "RETREATING"]),
                        )
                    )
                )
                .scalars()
                .all()
            )

            if not available_armies:
                return await ctx.send(
                    "You have no idle land armies available to march."
                )

            view = ArmySelectView(bot=self.bot, armies=available_armies)

            await ctx.send(
                "**Step 1: Select an army to move.**",
                view=view,
                ephemeral=True,
            )

    @commands.command(name="redirect")
    @commands.check(is_in_house_channel)
    async def redirect(self, ctx):
        """Initiates the interactive UI to redirect a moving army or fleet."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game found.")

            player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )

            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not have a house to command.")

            moving_units = (
                (
                    await session.execute(
                        select(Army).where(
                            Army.house_id == player.claimed_house_id,
                            Army.status.in_(["MARCHING", "SAILING"]),
                        )
                    )
                )
                .scalars()
                .all()
            )

            if not moving_units:
                return await ctx.send("You have no moving units to redirect.")

            now = datetime.datetime.now(datetime.timezone.utc)
            valid_units = []

            for unit in moving_units:
                dep_time = unit.departure_time
                if dep_time and dep_time.tzinfo is None:
                    dep_time = dep_time.replace(tzinfo=datetime.timezone.utc)

                if dep_time and dep_time > now:
                    continue

                valid_units.append(unit)

            if not valid_units:
                return await ctx.send(
                    "You have no active moving units to redirect (units inside moving fleets cannot be redirected individually)."
                )

            view = RedirectSelectView(bot=self.bot, armies=valid_units)
            await ctx.send(
                "**Step 1: Select a unit to redirect.**",
                view=view,
                ephemeral=True,
            )

    @commands.command(name="army")
    @commands.check(is_in_house_channel)
    async def army_details(self, ctx):
        """
        Detailed military report, paginated for large forces.
        """
        discord_id = ctx.author.id
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == discord_id, GamePlayer.game_id == game.game_id
                )
                .options(selectinload(GamePlayer.house))
            )
            player = (await session.execute(stmt)).scalars().first()

            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not have a house.")

            stmt_a = (
                select(Army)
                .where(
                    Army.house_id == player.claimed_house_id,
                    Army.status != "EMBARKED",
                )
                .order_by(Army.status.desc(), Army.troop_count.desc())
            )
            armies = (await session.execute(stmt_a)).scalars().all()

            if not armies:
                return await ctx.send("You have no military forces.")

            army_chunks = [armies[i : i + 10] for i in range(0, len(armies), 10)]
            embeds = []

            now = datetime.datetime.now(datetime.timezone.utc)

            for chunk in army_chunks:
                embed = discord.Embed(
                    title=f"⚔️ Military Report: House {player.house.name}",
                    color=discord.Color.red(),
                )

                for army in chunk:
                    if army.departure_time:
                        dep_time = army.departure_time
                        if dep_time.tzinfo is None:
                            dep_time = dep_time.replace(tzinfo=datetime.timezone.utc)

                        if dep_time > now:
                            continue

                    if army.status in ["MARCHING", "SAILING"]:
                        if army.arrival_time:
                            arr_time = army.arrival_time
                            if arr_time.tzinfo is None:
                                arr_time = arr_time.replace(
                                    tzinfo=datetime.timezone.utc
                                )

                            if arr_time > now:
                                remaining = arr_time - now
                                hours, rem = divmod(
                                    int(remaining.total_seconds()), 3600
                                )
                                minutes, _ = divmod(rem, 60)
                                time_str = f"{hours}h {minutes}m"
                            else:
                                time_str = "Arriving..."
                        else:
                            time_str = "???"

                        status_icon = "🦶" if army.status == "MARCHING" else "⛵"
                        status_str = f"{status_icon} {army.status} (ETA: {time_str})"
                    else:
                        status_str = f"🟢 {army.status}"

                    comp_items = []
                    if army.composition:
                        for k, v in army.composition.items():
                            if v <= 0:
                                continue
                            if army.army_type == "SEA" and k.lower() in [
                                "ships",
                                "ship",
                                "galley",
                                "galleys",
                            ]:
                                continue
                            comp_items.append(f"{k.title()[:3]}: {v}")

                    comp_str = " | ".join(comp_items)
                    if not comp_str:
                        comp_str = "-"

                    loc_str = f"{army.location_x:.0f}, {army.location_y:.0f}"

                    cargo_str = ""
                    if army.army_type == "SEA":
                        count_label = "Ships"
                        cargo_data = {}
                        if army.cargo:
                            if isinstance(army.cargo, dict):
                                cargo_data = army.cargo
                            elif isinstance(army.cargo, str):
                                try:
                                    cargo_data = json.loads(army.cargo)
                                except:
                                    pass

                        if cargo_data.get("troop_count", 0) > 0:
                            cargo_str = (
                                f"\n📦 **Cargo:** {cargo_data['troop_count']} men"
                            )
                    else:
                        count_label = "Troops"

                    embed.add_field(
                        name=f"{army.commander_name} (ID: {army.army_id})",
                        value=f"**Status:** {status_str}\n**{count_label}:** {army.troop_count}{cargo_str}\n**Comp:** {comp_str}\n**Location:** {loc_str}",
                        inline=False,
                    )

                embeds.append(embed)

            if embeds:
                if len(embeds) == 1:
                    await ctx.send(embed=embeds[0])
                else:
                    view = Paginator(embeds)
                    await ctx.send(embed=embeds[0], view=view)

    @commands.command(name="split")
    @commands.check(is_in_house_channel)
    async def split(self, ctx, army_id: int, amount: int, *, new_name: str):
        """Splits an army. Usage: !split [ID] [Amount] [Name]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()

            service = WarfareService(session)
            success, msg = await service.split_army(
                game.game_id, user.user_id, army_id, amount, new_name
            )
            await ctx.send(msg)

    @commands.command(name="merge")
    @commands.check(is_in_house_channel)
    async def merge(self, ctx, army_id_1: int, army_id_2: int):
        """Merges two armies. Usage: !merge [ID1] [ID2]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()

            service = WarfareService(session)
            success, msg = await service.merge_armies(
                game.game_id, user.user_id, army_id_1, army_id_2
            )
            await ctx.send(msg)

    @commands.command(name="form_coalition")
    @commands.check(is_in_house_channel)
    async def form_coalition(self, ctx, new_name: str, *army_ids: int):
        """
        Merges multiple armies.
        - If you own all armies, they merge instantly.
        - If armies are owned by multiple players, a consent proposal is created.
        Usage: !form_coalition "Name" 101 102 103
        """
        if not army_ids:
            return await ctx.send("❌ You must provide at least two army IDs.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game found.")

            armies = await ArmyRepo.get_armies_by_ids(session, list(army_ids))
            if len(armies) != len(army_ids):
                return await ctx.send("❌ One or more army IDs are invalid.")

            owners_map = {}
            for army in armies:
                player_owner = await session.scalar(
                    select(GamePlayer)
                    .join(User)
                    .where(
                        GamePlayer.claimed_house_id == army.house_id,
                        GamePlayer.is_primary == True,
                    )
                    .options(selectinload(GamePlayer.user))
                )
                if not player_owner or not player_owner.user:
                    return await ctx.send(
                        f"❌ Could not find a player for House {army.house.name}."
                    )

                owner_id = player_owner.user.discord_id
                if owner_id not in owners_map:
                    owners_map[owner_id] = []
                owners_map[owner_id].append(army)

            if len(owners_map) == 1 and ctx.author.id in owners_map:
                await ctx.send("🤝 Merging your own units...")
                service = WarfareService(session)
                success, msg = await service.form_coalition(
                    game.game_id, ctx.author.id, new_name, army_ids
                )
                await ctx.send(msg)

            else:
                targets_map = {}
                mentions = []
                for owner_id, owned_armies in owners_map.items():
                    try:
                        member = await ctx.guild.fetch_member(owner_id)
                    except discord.NotFound:
                        return await ctx.send(
                            f"❌ Player with ID {owner_id} not found in this server."
                        )
                    except discord.HTTPException:
                        return await ctx.send(
                            f"❌ Discord API error while fetching user {owner_id}."
                        )

                    targets_map[member] = owned_armies

                    if member != ctx.author:
                        mentions.append(member.mention)

                view = CoalitionConsentView(
                    self.bot, ctx.author, targets_map, game.game_id, new_name, army_ids
                )
                await ctx.send(
                    f"A coalition has been proposed! {', '.join(mentions)}",
                    embed=view.create_embed(),
                    view=view,
                )

    @commands.command(name="disband")
    @commands.check(is_in_house_channel)
    async def disband_coalition(self, ctx, army_id: int):
        """Disbands a coalition. Usage: !disband [ID]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()

            service = WarfareService(session)
            success, msg = await service.disband_coalition(
                game.game_id, user.user_id, army_id
            )
            await ctx.send(msg)

    # --- ADMIN/GM COMMANDS ---
    @commands.command(name="rush")
    @commands.has_permissions(
        administrator=True
    )  # Existing admin check, effectively GM
    async def admin_rush(self, ctx, army_id: int):
        """GM Tool: Force an army to arrive by running its task now."""
        resolve_army_arrival.delay(army_id)
        await ctx.send(
            f"⚡ **Divine Wind:** Arrival task for Army {army_id} sent to worker immediately."
        )

    @commands.command(name="rush_all")
    @commands.has_permissions(
        administrator=True
    )  # Existing admin check, effectively GM
    async def rush_all(self, ctx, *, destination: str = None):
        """
        GM Tool: Instantly completes marches.
        Usage: !rush_all Winterfell (Rushes everyone marching to Winterfell)
        Usage: !rush_all (Rushes EVERY marching army in the game)
        """
        count = 0
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = select(Army).where(
                Army.game_id == game.game_id, Army.status.in_(["MARCHING", "SAILING"])
            )

            if destination:
                service = WarfareService(session)
                loc = await service._get_location_from_db(game.game_id, destination)
                if not loc:
                    await ctx.send(f"❌ Location **{destination}** not found.")
                    return

                stmt = stmt.where(
                    Army.destination_x == loc["x"], Army.destination_y == loc["y"]
                )

            armies = (await session.execute(stmt)).scalars().all()

            if not armies:
                await ctx.send("⚠️ No armies found matching those criteria.")
                return

            for army in armies:
                resolve_army_arrival.delay(army.army_id)
                count += 1

            target_msg = (
                f"to **{destination}**" if destination else "in the **entire world**"
            )
            await ctx.send(
                f"⚡ **Divine Wind:** Rushed **{count}** armies {target_msg}."
            )

    @commands.group(name="worldrule", invoke_without_command=True)
    @commands.has_permissions(
        administrator=True
    )  # Existing admin check, effectively GM
    async def worldrule(self, ctx):
        """Parent command for managing world rules."""
        await ctx.send("Subcommands: `setbridge`, `setrivers`, `setsea`.")

    @worldrule.command(name="setbridge")
    @commands.has_permissions(administrator=True)
    async def set_bridge(self, ctx, bridge_name: str, status: str):
        bridge_map = {
            "twins": "twins_open",
            "rubyford": "rubyford_open",
            "bitterbridge": "bitterbridge_open",
        }
        rule_name = bridge_map.get(bridge_name.lower())
        if not rule_name:
            return await ctx.send("❌ Invalid bridge name.")
        is_enabled = status.lower() in ["on", "open", "enabled"]
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return
            msg = await WarfareService(session).set_world_rule(
                game.game_id, rule_name, is_enabled
            )
            await ctx.send(msg)

    @worldrule.command(name="setrivers")
    @commands.has_permissions(administrator=True)
    async def set_rivers(self, ctx, status: str):
        is_enabled = status.lower() in ["impassable", "on", "enabled"]
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return
            msg = await WarfareService(session).set_world_rule(
                game.game_id, "rivers_impassable", is_enabled
            )
            await ctx.send(msg)

    @worldrule.command(name="setsea")
    @commands.has_permissions(administrator=True)
    async def set_sea(self, ctx, status: str):
        is_enabled = status.lower() in ["allowed", "on", "enabled"]
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return
            msg = await WarfareService(session).set_world_rule(
                game.game_id, "sea_travel_allowed", is_enabled
            )
            await ctx.send(msg)

    @commands.command(name="sail")
    @commands.check(is_in_house_channel)
    async def sail(self, ctx):
        """Initiates the interactive sail order UI for SEA armies."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )
            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not have a house to command.")

            # Fetch only the user's available SEA armies (fleets)
            available_fleets = (
                (
                    await session.execute(
                        select(Army).where(
                            Army.house_id == player.claimed_house_id,
                            Army.army_type == "SEA",
                            Army.status.in_(
                                ["IDLE", "DOCKED", "GARRISONED", "RETREATING"]
                            ),  # Or whatever your idle statuses are
                        )
                    )
                )
                .scalars()
                .all()
            )

            if not available_fleets:
                return await ctx.send("You have no fleets available to sail.")

            view = FleetSelectView(bot=self.bot, fleets=available_fleets)
            await ctx.send(
                "**Step 1: Select a fleet to command.**",
                view=view,
            )

    @commands.command(name="stop", aliases=["halt"])
    @commands.check(is_in_house_channel)
    async def stop(self, ctx, army_id: int):
        """Stops a moving army or fleet immediately. Usage: !stop [ID]"""
        async with get_session() as session:
            # 1. Setup Game & User
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return await ctx.send("❌ You are not registered.")

            # 2. Call Service
            service = WarfareService(session)
            success, msg = await service.stop_march(game.game_id, user.user_id, army_id)

            # 3. Handle "Ghost Army" Cleanup (The Fleet Patch)
            # If we successfully stopped a FLEET, we must check if there was a
            # pre-scheduled Land Army waiting for it (Ghost Army) and delete it.
            if success:
                # We re-fetch the army to check if it was a Fleet
                army = await ArmyRepo.get_army_by_id(session, army_id)
                if army and army.army_type == "SEA":
                    # Look for any future marches for this house
                    import datetime

                    now = datetime.datetime.now(datetime.timezone.utc)
                    stmt_ghost = select(Army).where(
                        Army.house_id == army.house_id,
                        Army.army_type == "LAND",
                        Army.status == "MARCHING",
                        Army.departure_time > now,
                    )
                    ghosts = (await session.execute(stmt_ghost)).scalars().all()

                    if ghosts:
                        for ghost in ghosts:
                            # Put troops back into the fleet cargo
                            if not army.cargo:
                                army.cargo = {
                                    "commander": ghost.commander_name,
                                    "troop_count": ghost.troop_count,
                                    "composition": ghost.composition,
                                }
                            # Delete the ghost
                            await session.delete(ghost)

                        await session.commit()
                        msg += "\n(⚠️ Cancelled scheduled disembarkation orders)"

            await ctx.send(msg)

    @commands.command(name="embark")
    @commands.check(is_in_house_channel)
    async def embark(self, ctx, land_army_id: int, fleet_id: int):
        """
        Loads a land army onto a fleet at the same location.
        Usage: !embark [Land_Army_ID] [Fleet_ID]
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return

            service = WarfareService(session)
            success, msg = await service.embark_army(
                game.game_id, user.user_id, land_army_id, fleet_id
            )
            await ctx.send(msg)

    @commands.command(name="disembark")
    @commands.check(is_in_house_channel)
    async def disembark(self, ctx, army_id: int):
        """
        Unloads troops from a fleet to the current location.
        Usage: !disembark [Fleet_ID]
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return

            service = WarfareService(session)
            success, msg = await service.disembark_army(
                game.game_id, user.user_id, army_id
            )
            await ctx.send(msg)

    @commands.command(name="recruit")
    @commands.check(
        recruitment_is_enabled
    )  # This now correctly checks the manpower_enabled flag
    @commands.check(is_in_house_channel)
    async def recruit(self, ctx, fief_name: str, amount: int):
        """
        Recruit troops from your manpower pool into a garrison.
        Usage: !recruit Winterfell 1000
        """
        # NO CHANGES NEEDED HERE. The decorators and service handle everything.
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return

            service = WarfareService(session)
            success, msg = await service.recruit_troops(
                game.game_id, user.user_id, fief_name, amount
            )
            await ctx.send(msg)

    # --- GM Warfare Group Commands ---
    @commands.group(name="gm_war", invoke_without_command=True)
    @commands.check(is_gm)
    async def gm_war(self, ctx):
        """GM commands for warfare and army management for NPCs."""
        await ctx.send(
            "GM Warfare Subcommands: `march`, `sail`, `stop`, `split`, `merge`, `form_coalition`, `disband_coalition`, `embark`, `disembark`, `recruit`, `occupy`, `redirect`."
        )

    @gm_war.command(name="march")
    @commands.check(is_gm)
    async def gm_march(
        self,
        ctx,
        target_house_id: int,
        army_id: int = None,
        dest_name: str = None,
        units_input: str = "all",
        commander: str = None,
        gold_to_carry: int = 0,
        *,
        waypoints: str = None,
    ):
        """
        GM: March an NPC army.
        Interactive: !gm_war march [HouseID]
        Manual: !gm_war march [HouseID] [ArmyID] [Destination] ...
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # --- INTERACTIVE MODE ---
            if army_id is None:
                armies = (
                    (
                        await session.execute(
                            select(Army).where(
                                Army.house_id == target_house_id,
                                Army.status.in_(["IDLE", "GARRISONED", "RETREATING"]),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

                if not armies:
                    return await ctx.send(
                        f"❌ House {target_house_id} has no idle armies."
                    )

                # Import view locally to avoid circular imports if necessary
                from app.ui.gm_march_view import GMMarchArmySelectView

                view = GMMarchArmySelectView(self.bot, armies, target_house_id)
                await ctx.send(
                    f"👑 **GM Command:** Commanding House {target_house_id}", view=view
                )
                return

            # --- MANUAL MODE ---
            if not dest_name:
                return await ctx.send(
                    "❌ Usage: `!gm_war march [HouseID] [ArmyID] [Destination]`"
                )

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found.")

            service = WarfareService(session)
            success, result_or_msg, fog_msg = await service.march_army(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                identifier=str(army_id),
                dest_name=dest_name,
                units_input=units_input,
                commander=commander,
                gold_to_carry=gold_to_carry,
                waypoints=waypoints,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )

            if success:
                # 1. GM Feedback (Private)
                response_embed = discord.Embed(
                    title=f"✅ GM Command: NPC March Order (House {target_house_id})",
                    description=f"**{result_or_msg['commander']}** ({result_or_msg['count']} men) -> **{result_or_msg['destination']}**.",
                    color=discord.Color.green(),
                )
                response_embed.add_field(
                    name="Est. Time", value=result_or_msg["time"], inline=True
                )
                response_embed.add_field(
                    name="Gold",
                    value=str(result_or_msg.get("gold_carried", 0)),
                    inline=True,
                )

                if result_or_msg.get("image"):
                    image_file = discord.File(
                        result_or_msg["image"], filename="journey_gm.png"
                    )
                    response_embed.set_image(url="attachment://journey_gm.png")
                    await ctx.send(file=image_file, embed=response_embed)
                    result_or_msg["image"].close()
                else:
                    await ctx.send(embed=response_embed)

                # 2. Public Fog of War (general-movements)
                if fog_msg:
                    # FIX: Correct channel name
                    gen_channel = discord.utils.get(
                        ctx.guild.text_channels, name="general-movements"
                    )
                    if gen_channel:
                        # FIX: Send raw message so it looks like a rumor, not a GM log
                        await gen_channel.send(fog_msg)
            else:
                await ctx.send(f"❌ GM Command Failed: {result_or_msg}")

    @gm_war.command(name="sail")
    @commands.check(is_gm)
    async def gm_sail(
        self,
        ctx,
        target_house_id: int,
        fleet_id: int = None,  # Made Optional to trigger UI
        dest_name: str = None,  # Made Optional
        ships_input: str = "all",
        units_input: str = None,
        commander: str = None,
        gold_to_carry: int = 0,
        *,
        waypoints: str = None,
    ):
        """
        GM: Sail an NPC fleet.
        Interactive: !gm_war sail [HouseID]
        Manual: !gm_war sail [HouseID] [FleetID] [Dest] [Ships] [Units] ...
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # --- 1. INTERACTIVE MODE (UI) ---
            if fleet_id is None:
                # Fetch available fleets for this NPC house
                fleets = (
                    (
                        await session.execute(
                            select(Army).where(
                                Army.house_id == target_house_id,
                                Army.army_type == "SEA",
                                Army.status.in_(
                                    ["IDLE", "DOCKED", "GARRISONED", "RETREATING"]
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

                if not fleets:
                    return await ctx.send(
                        f"❌ House {target_house_id} has no available fleets."
                    )

                # Import locally to avoid circular imports
                from app.ui.gm_sail_view import GMFleetSelectView

                view = GMFleetSelectView(self.bot, fleets, target_house_id)
                await ctx.send(
                    f"👑 **GM Command:** Admiral for House {target_house_id}", view=view
                )
                return

            # --- 2. MANUAL MODE (Original Command) ---
            if not dest_name:
                return await ctx.send(
                    "❌ Manual Usage: `!gm_war sail [HouseID] [FleetID] [Destination]`"
                )

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found.")

            service = WarfareService(session)
            success, result_or_msg, fog_msg = await service.sail_fleet(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                fleet_id=fleet_id,
                ships_input=ships_input,
                dest_name=dest_name,
                units_input=units_input,
                commander=commander,
                gold_to_carry=gold_to_carry,
                waypoints=waypoints,
                is_gm_override=True,  # Override ownership
                acting_house_id=target_house_id,  # Act as NPC
            )

            if success:
                # GM Feedback (Private/Contextual)
                response_embed = discord.Embed(
                    title=f"✅ GM Sail Order: House {target_house_id}",
                    description=f"**{result_or_msg['commander']}** ({result_or_msg['count']} men) {result_or_msg.get('journey_summary', 'set sail')}.",
                    color=discord.Color.green(),
                )
                response_embed.add_field(
                    name="Est. Time", value=result_or_msg["time"], inline=True
                )
                response_embed.add_field(
                    name="Gold",
                    value=str(result_or_msg.get("gold_carried", 0)),
                    inline=True,
                )

                if result_or_msg.get("image"):
                    image_file = discord.File(
                        result_or_msg["image"], filename="journey_gm.png"
                    )
                    response_embed.set_image(url="attachment://journey_gm.png")
                    await ctx.send(file=image_file, embed=response_embed)
                    result_or_msg["image"].close()
                else:
                    await ctx.send(embed=response_embed)

                # Public Fog of War (Rumors) -> general-movements
                if fog_msg:
                    gen_channel = discord.utils.get(
                        ctx.guild.text_channels, name="general-movements"
                    )
                    if gen_channel:
                        # Send raw message (looks like a rumor)
                        await gen_channel.send(fog_msg)
            else:
                await ctx.send(f"❌ GM Command Failed: {result_or_msg}")

    @gm_war.command(name="stop")
    @commands.check(is_gm)
    async def gm_stop(self, ctx, target_house_id: int, army_id: int):
        """GM: Stop an NPC house's moving army/fleet. Usage: !gm_war stop [HouseID] [ArmyID]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.stop_march(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=army_id,
                is_admin=True,  # Use this to ensure it stops regardless of claimed_house_id
                is_gm_override=True,  # For consistency in signaling GM intent
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="split")
    @commands.check(is_gm)
    async def gm_split(
        self, ctx, target_house_id: int, army_id: int, amount: int, *, new_name: str
    ):
        """GM: Split an NPC house's army. Usage: !gm_war split [HouseID] [ArmyID] [Amount] [NewName]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.split_army(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=army_id,
                split_amount=amount,
                new_name=new_name,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="merge")
    @commands.check(is_gm)
    async def gm_merge(self, ctx, target_house_id: int, army_id_1: int, army_id_2: int):
        """GM: Merge two of an NPC house's armies. Usage: !gm_war merge [HouseID] [ArmyID1] [ArmyID2]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.merge_armies(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                id_1=army_id_1,
                id_2=army_id_2,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="form_coalition")
    @commands.check(is_gm)
    async def gm_form_coalition(
        self, ctx, target_house_id: int, new_name: str, *army_ids: int
    ):
        """GM: Form a coalition for an NPC house. All armies must belong to the target_house_id. Usage: !gm_war form_coalition [HouseID] "Name" 101 102"""
        if len(army_ids) < 2:
            return await ctx.send("❌ You must provide at least two army IDs.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game found.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            # When GM forms a coalition for an NPC, bypass_auth is essentially handled by is_gm_override
            success, msg = await service.form_coalition(
                game_id=game.game_id,
                leader_user_id=gm_user_obj.user_id,  # GM is the "leader" initiating the command
                new_name=new_name,
                army_ids=army_ids,
                bypass_auth=True,  # Allow GM to bypass the multi-player consent flow
                is_gm_override=True,  # Critical flag
                acting_house_id=target_house_id,  # Critical: specify the NPC house that owns the coalition
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="disband_coalition")
    @commands.check(is_gm)
    async def gm_disband_coalition(self, ctx, target_house_id: int, army_id: int):
        """GM: Disband a coalition belonging to an NPC house. Usage: !gm_war disband_coalition [HouseID] [CoalitionArmyID]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.disband_coalition(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=army_id,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="embark")
    @commands.check(is_gm)
    async def gm_embark(
        self, ctx, target_house_id: int, land_army_id: int, fleet_id: int
    ):
        """GM: Embark an NPC land army onto an NPC fleet. Usage: !gm_war embark [HouseID] [LandArmyID] [FleetID]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.embark_army(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                land_army_id=land_army_id,
                fleet_id=fleet_id,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="disembark")
    @commands.check(is_gm)
    async def gm_disembark(self, ctx, target_house_id: int, fleet_id: int):
        """GM: Disembark troops from an NPC fleet. Usage: !gm_war disembark [HouseID] [FleetID]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.disembark_army(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=fleet_id,  # The army_id parameter in disembark_army is actually the fleet_id
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="recruit")
    @commands.check(is_gm)
    async def gm_recruit(self, ctx, target_house_id: int, fief_name: str, amount: int):
        """GM: Recruit troops for an NPC house. Usage: !gm_war recruit [HouseID] [FiefName] [Amount]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.recruit_troops(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                fief_name=fief_name,
                amount=amount,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @commands.command()
    async def occupy(self, ctx, army_id: int):
        """Occupies a Fief if it is undefended."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                await ctx.send("❌ No active game found in this server.")
                return

            service = WarfareService(session)
            success, msg = await service.occupy_fief(
                game.game_id, ctx.author.id, army_id
            )

            await ctx.send(msg)

            # --- ADDED: Public Event Notification ---
            if success:
                news_chan = discord.utils.get(
                    ctx.guild.text_channels, name="news-and-events"
                )
                if news_chan:
                    embed = discord.Embed(
                        title="🏰 Fief Occupied!",
                        description=msg,
                        color=discord.Color.gold(),
                    )
                    await news_chan.send(embed=embed)

    # @gm_war.command(name="occupy")
    # @commands.check(is_gm)
    # async def gm_occupy(self, ctx, target_house_id: int, army_id: int):
    #     """GM: Make an NPC army occupy an undefended fief. Usage: !gm_war occupy [HouseID] [ArmyID]"""
    #     async with get_session() as session:
    #         game = await GameRepo.get_active_game(session, ctx.guild.id)
    #         if not game:
    #             return await ctx.send("❌ No active game.")

    #         gm_user_obj = await session.scalar(
    #             select(User).where(User.discord_id == ctx.author.id)
    #         )
    #         if not gm_user_obj:
    #             return await ctx.send("❌ GM user not found in DB.")

    #         service = WarfareService(session)
    #         success, msg = await service.occupy_fief(
    #             game_id=game.game_id,
    #             user_id=gm_user_obj.user_id,
    #             army_id=army_id,
    #             is_gm_override=True,
    #             acting_house_id=target_house_id,
    #         )
    #         await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="occupy")
    @commands.check(is_gm)
    async def gm_occupy(self, ctx, target_house_id: int, army_id: int):
        """GM: Make an NPC army occupy an undefended fief."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.occupy_fief(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=army_id,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

            # --- ADDED: Public Event Notification ---
            if success:
                news_chan = discord.utils.get(
                    ctx.guild.text_channels, name="news-and-events"
                )
                if news_chan:
                    embed = discord.Embed(
                        title="🏰 Fief Occupied (GM Event)",
                        description=msg,
                        color=discord.Color.gold(),
                    )
                    await news_chan.send(embed=embed)

    @gm_war.command(name="redirect")
    @commands.check(is_gm)
    async def gm_redirect(
        self,
        ctx,
        target_house_id: int,
        army_id: int,
        new_dest_name: str,
        *,
        new_waypoints: str = None,
    ):
        """GM: Redirect a moving NPC army/fleet. Usage: !gm_war redirect [HouseID] [ArmyID] [NewDestination] [NewWaypoints]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, result_or_msg, fog_msg = await service.redirect_army(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=army_id,
                new_dest_name=new_dest_name,
                new_waypoints=new_waypoints,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            if success:
                response_embed = discord.Embed(
                    title=f"✅ GM Command: NPC Redirect Order for House ID {target_house_id}",
                    description=f"**{result_or_msg.get('commander', 'Unit')}** ({result_or_msg.get('count', 'N/A')} troops) redirected to **{result_or_msg.get('destination', 'N/A')}**.",
                    color=discord.Color.green(),
                )
                response_embed.add_field(
                    name="Est. Time",
                    value=result_or_msg.get("time", "N/A"),
                    inline=True,
                )
                if result_or_msg.get("image"):
                    image_file = discord.File(
                        result_or_msg["image"], filename="redirect_gm.png"
                    )
                    response_embed.set_image(url="attachment://redirect_gm.png")
                    await ctx.send(file=image_file, embed=response_embed)
                    result_or_msg["image"].close()
                else:
                    await ctx.send(embed=response_embed)

                if fog_msg:
                    gm_channel = discord.utils.get(
                        ctx.guild.text_channels, name="gm-alerts"
                    )
                    if gm_channel:
                        await gm_channel.send(
                            f"🌐 **FOW Report for GM (House ID {target_house_id}):** {fog_msg}"
                        )
            else:
                await ctx.send(f"❌ GM Command Failed: {result_or_msg}")

    # --- END GM COMMANDS ---


async def setup(bot):
    await bot.add_cog(WarfareCog(bot))
