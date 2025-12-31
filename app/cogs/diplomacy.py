# # import discord
# # from discord.ext import commands
# # from app.db.db_manager import get_session
# # from app.db.repositories import GameRepo
# # from app.services.diplomacy_service import DiplomacyService
# # from app.db.models import User, GamePlayer, House, Army, Character
# # from sqlalchemy import select
# # from sqlalchemy.orm import selectinload
# # from app.ui.banner_view import BannerControlView
# # from app.ui.paginator import Paginator
# # from app.ui.social_views import ProposalView
# # import re
# # from app.db.repositories import GameRepo, FiefRepo, ArmyRepo
# # from app.services.diplomacy_service import DiplomacyService

# # from app.services.travel_calculator import calculate_travel_duration
# # from app.db.models import Game, GamePlayer, User  # Import necessary models
# # import datetime
# # from typing import List, Literal
# # from app.db.models import Game, GamePlayer, User, House, PendingBannerCall
# # from app.db.repositories import GameRepo
# # from app.services.diplomacy_service import DiplomacyService
# # from app.ui.banner_view import BannerControlView  # New impo
# # from app.checks import is_in_house_channel

# import discord
# from discord.ext import commands
# from sqlalchemy import select
# from sqlalchemy.orm import selectinload
# import re
# import datetime
# from typing import List, Literal

# from app.db.db_manager import get_session
# from app.db.models import User, GamePlayer, House, Character, PendingBannerCall
# from app.db.repositories import GameRepo
# from app.services.diplomacy_service import DiplomacyService
# from app.ui.banner_view import BannerControlView
# from app.ui.paginator import Paginator
# from app.ui.social_views import ProposalView
# from app.checks import is_in_house_channel


# class DiplomacyCog(commands.Cog):
#     def __init__(self, bot):
#         self.bot = bot

#     # async def _handle_union_proposal(
#     #     self, ctx: commands.Context, query: str, action_name: str, icon: str
#     # ):
#     #     """A shared helper to process both marriage and betrothal proposals."""

#     #     parts = re.split(r"\s+to\s+", query, maxsplit=1, flags=re.IGNORECASE)
#     #     if len(parts) != 2:
#     #         return await ctx.send(
#     #             f'❌ Format: `!{ctx.invoked_with} "[Person A]" to "[Person B]"`'
#     #         )

#     #     char_a_name = parts[0].strip("\"' ")
#     #     char_b_name = parts[1].strip("\"' ")

#     #     async with get_session() as session:
#     #         game = await GameRepo.get_active_game(session, ctx.guild.id)
#     #         if not game:
#     #             return

#     #         service = DiplomacyService(session)

#     #         # 1. Find the player arranging the union
#     #         arranger_player = await session.scalar(
#     #             select(GamePlayer)
#     #             .join(User)
#     #             .where(
#     #                 User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#     #             )
#     #         )
#     #         if not arranger_player:
#     #             return await ctx.send("❌ You are not a player in this game.")

#     #         # 2. Find or create the characters involved
#     #         char_a = await service.find_or_create_char(game.game_id, char_a_name)
#     #         char_b = await service.find_or_create_char(game.game_id, char_b_name)
#     #         if not char_a or not char_b:
#     #             return await ctx.send(
#     #                 "❌ One or more characters could not be found or created. Ensure their House exists."
#     #             )
#     #         if char_a.spouse_id or char_b.spouse_id:
#     #             return await ctx.send("❌ One of the characters is already married.")

#     #         # 3. Authority Check: Can the arranger act for Character A?
#     #         has_authority = await service.check_marriage_authority(
#     #             arranger_player, char_a
#     #         )
#     #         if not has_authority:
#     #             return await ctx.send(
#     #                 f"❌ You do not have the authority to arrange a {action_name.lower()} for **{char_a.name}**."
#     #             )

#     #         # 4. Find Consenter: Who needs to approve for Character B?
#     #         consenting_user = await service.find_consenting_player(char_b)

#     #         # --- Callback function to run on success ---
#     #         async def on_accept(interaction: discord.Interaction):
#     #             success, msg = await service.execute_marriage(
#     #                 game.game_id, char_a.name, char_b.name
#     #             )
#     #             # Post to a public news/marriages channel
#     #             news_channel = discord.utils.get(
#     #                 ctx.guild.text_channels, name="marriages"
#     #             )
#     #             if success and news_channel:
#     #                 await news_channel.send(f"{icon} {msg}")
#     #             # Update the original proposal message
#     #             final_embed = interaction.message.embeds[0]
#     #             final_embed.set_footer(text=f"✅ {action_name} Confirmed!")
#     #             final_embed.color = discord.Color.green()
#     #             await interaction.edit_original_message(embed=final_embed, view=None)

#     #         # 5. SCENARIO ROUTING
#     #         # Case A: An NPC is marrying an NPC from an un-claimed house -> GM Approval
#     #         if not consenting_user:
#     #             gm_channel = discord.utils.get(
#     #                 ctx.guild.text_channels, name="gm-alerts"
#     #             )
#     #             if not gm_channel:
#     #                 return await ctx.send("❌ GM alerts channel not found.")

#     #             proposal_embed = discord.Embed(
#     #                 title=f"{icon} GM Approval: {action_name}",
#     #                 description=f"**{ctx.author.display_name}** requests to arrange a union between **{char_a.name}** and the NPC **{char_b.name}**.",
#     #                 color=discord.Color.dark_purple(),
#     #             )
#     #             view = ProposalView(
#     #                 initiator=ctx.author,
#     #                 consenter=ctx.author,  # GM is the consenter
#     #                 action_name=action_name,
#     #                 proposal_embed=proposal_embed,
#     #                 on_accept_callback=on_accept,
#     #                 is_gm_approval=True,
#     #             )
#     #             await gm_channel.send(embed=proposal_embed, view=view)
#     #             return await ctx.send(
#     #                 "✅ Your proposal for the NPC has been sent to the GMs for approval."
#     #             )

#     #         # Case B: The arranger has authority over both parties -> Auto-Accept
#     #         elif consenting_user.discord_id == ctx.author.id:
#     #             success, msg = await service.execute_marriage(
#     #                 game.game_id, char_a.name, char_b.name
#     #             )
#     #             news_channel = discord.utils.get(
#     #                 ctx.guild.text_channels, name="marriages"
#     #             )
#     #             if success and news_channel:
#     #                 await news_channel.send(f"{icon} {msg}")
#     #             return await ctx.send(msg)

#     #         # Case C: Another player must consent
#     #         else:
#     #             consenter_member = ctx.guild.get_member(consenting_user.discord_id)
#     #             if not consenter_member:
#     #                 return await ctx.send(
#     #                     f"❌ The required consenter ({consenting_user.name}) is not in this server."
#     #                 )

#     #             proposal_embed = discord.Embed(
#     #                 title=f"{icon} {action_name} Proposal",
#     #                 description=f"**{ctx.author.display_name}** proposes a union between **{char_a.name}** and **{char_b.name}**.",
#     #                 color=discord.Color.purple(),
#     #             )
#     #             view = ProposalView(
#     #                 initiator=ctx.author,
#     #                 consenter=consenter_member,
#     #                 action_name=action_name,
#     #                 proposal_embed=proposal_embed,
#     #                 on_accept_callback=on_accept,
#     #             )
#     #             await ctx.send(
#     #                 f"{consenter_member.mention}, a proposal awaits your decision.",
#     #                 embed=proposal_embed,
#     #                 view=view,
#     #             )

#     #         break  # Exit session loop

#     # In your DiplomacyCog class

#     async def _handle_union_proposal(
#         self, ctx: commands.Context, query: str, action_name: str, icon: str
#     ):
#         """A shared helper to process both marriage and betrothal proposals."""
#         parts = re.split(r"\s+to\s+", query, maxsplit=1, flags=re.IGNORECASE)
#         if len(parts) != 2:
#             return await ctx.send(
#                 f'❌ Format: `!{ctx.invoked_with} "[Person A]" to "[Person B]"`'
#             )

#         char_a_name, char_b_name = parts[0].strip("\"' "), parts[1].strip("\"' ")

#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return

#             service = DiplomacyService(session)

#             arranger_player = await session.scalar(
#                 select(GamePlayer)
#                 .join(User)
#                 .where(
#                     User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#                 )
#             )
#             if not arranger_player:
#                 return await ctx.send("❌ You are not a player in this game.")

#             char_a = await service.find_or_create_char(game.game_id, char_a_name)
#             char_b = await service.find_or_create_char(game.game_id, char_b_name)
#             if not char_a or not char_b:
#                 return await ctx.send(
#                     "❌ One or more characters/houses could not be found."
#                 )
#             if char_a.spouse_id or char_b.spouse_id:
#                 return await ctx.send("❌ One of the characters is already married.")

#             if not await service.check_marriage_authority(arranger_player, char_a):
#                 return await ctx.send(
#                     f"❌ You do not have the authority to arrange a {action_name.lower()} for **{char_a.name}**."
#                 )

#             consenting_player_obj = await service.find_consenting_player(char_b)

#             async def on_accept(interaction: discord.Interaction):
#                 success, msg = await service.execute_marriage(
#                     game.game_id, char_a.name, char_b.name
#                 )
#                 news_channel = discord.utils.get(
#                     interaction.guild.text_channels, name="marriages"
#                 )
#                 if success and news_channel:
#                     await news_channel.send(f"{icon} {msg}")

#                 final_embed = interaction.message.embeds[0]
#                 final_embed.set_footer(text=f"✅ {action_name} Confirmed!")
#                 final_embed.color = discord.Color.green()
#                 await interaction.edit_original_message(embed=final_embed, view=None)

#             # Case A: GM Approval needed
#             if not consenting_player_obj:
#                 gm_channel = discord.utils.get(
#                     ctx.guild.text_channels, name="gm-alerts"
#                 )
#                 if not gm_channel:
#                     return await ctx.send("❌ GM alerts channel not found.")
#                 proposal_embed = discord.Embed(
#                     title=f"{icon} GM Approval: {action_name}",
#                     description=f"**{ctx.author.display_name}** requests a union between **{char_a.name}** and the NPC **{char_b.name}**.",
#                     color=discord.Color.dark_purple(),
#                 )
#                 view = ProposalView(
#                     initiator=ctx.author,
#                     consenter=ctx.author,
#                     action_name=action_name,
#                     proposal_embed=proposal_embed,
#                     on_accept_callback=on_accept,
#                     is_gm_approval=True,
#                 )
#                 await gm_channel.send(embed=proposal_embed, view=view)
#                 return await ctx.send(
#                     "✅ Your proposal has been sent to the GMs for approval."
#                 )

#             # Case B: Arranger has authority over both (Auto-Accept)
#             elif consenting_player_obj.user.discord_id == ctx.author.id:
#                 success, msg = await service.execute_marriage(
#                     game.game_id, char_a.name, char_b.name
#                 )
#                 news_channel = discord.utils.get(
#                     ctx.guild.text_channels, name="marriages"
#                 )
#                 if success and news_channel:
#                     await news_channel.send(f"{icon} {msg}")
#                 return await ctx.send(msg)

#             # Case C: Another player must consent
#             else:
#                 # --- THIS IS THE CORE FIX ---
#                 try:
#                     # Use fetch_member for reliability instead of get_member
#                     consenter_member = await ctx.guild.fetch_member(
#                         consenting_player_obj.user.discord_id
#                     )
#                 except discord.NotFound:
#                     consenter_member = None
#                 # --- END OF FIX ---

#                 if not consenter_member:
#                     # Get a user-friendly name for the error message
#                     consenter_name = (
#                         consenting_player_obj.character.name
#                         if consenting_player_obj.character
#                         else (
#                             consenting_player_obj.house.name
#                             if consenting_player_obj.house
#                             else f"User ID {consenting_player_obj.user.discord_id}"
#                         )
#                     )
#                     return await ctx.send(
#                         f"❌ The required consenter for this union ({consenter_name}) could not be found in this server."
#                     )

#                 proposal_embed = discord.Embed(
#                     title=f"{icon} {action_name} Proposal",
#                     description=f"**{ctx.author.display_name}** proposes a union between **{char_a.name}** and **{char_b.name}**.",
#                     color=discord.Color.purple(),
#                 )
#                 view = ProposalView(
#                     initiator=ctx.author,
#                     consenter=consenter_member,
#                     action_name=action_name,
#                     proposal_embed=proposal_embed,
#                     on_accept_callback=on_accept,
#                 )
#                 await ctx.send(
#                     f"{consenter_member.mention}, a proposal awaits your decision.",
#                     embed=proposal_embed,
#                     view=view,
#                 )

#     async def _send_gm_levy_alert(
#         self,
#         *,  # Use keyword-only arguments for clarity
#         ctx: commands.Context,
#         liege_player: GamePlayer,
#         rally_point: str,
#         results: List[str],
#         player_vassals: List[dict],
#         levy_type: Literal["Land", "Naval"],
#     ):
#         """
#         A robust, reusable helper to send detailed alerts to the #gm-alerts channel.
#         """
#         # --- CORE CHANGE: Find the channel by its expected name ---
#         channel = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")
#         # --- END CHANGE ---

#         if not channel:
#             # Silently fail in the console without interrupting the user's command.
#             print(
#                 f"GM Alert failed: Could not find channel named 'gm-alerts' in guild {ctx.guild.id}"
#             )
#             # Optional: Send a one-time warning to the user if the channel is missing.
#             # await ctx.send("⚠️ **Admin Note:** GM alert channel `#gm-alerts` not found.", delete_after=20)
#             return

#         # Process the results for a clean summary
#         success_count = sum(1 for r in results if r.startswith("✅"))
#         fail_count = sum(1 for r in results if r.startswith("⚠️"))
#         no_units_count = sum(1 for r in results if r.startswith(("🍂", "💨")))

#         title = (
#             "GM Alert: Banners Called"
#             if levy_type == "Land"
#             else "GM Alert: Naval Levies Called"
#         )
#         color = (
#             discord.Color.gold() if levy_type == "Land" else discord.Color.dark_blue()
#         )

#         embed = discord.Embed(
#             title=title,
#             color=color,
#             timestamp=datetime.datetime.now(datetime.timezone.utc),
#         )
#         embed.description = f"**{liege_player.house.name}** has summoned their {levy_type.lower()} vassals to **{rally_point}**."
#         embed.add_field(
#             name="Liege Lord",
#             value=f"{ctx.author.mention} (`{ctx.author.display_name}`)",
#             inline=False,
#         )

#         unit_type = "Levies" if levy_type == "Land" else "Fleets"
#         summary_value = (
#             f"Player Vassals Notified: **{len(player_vassals)}**\n"
#             f"NPC {unit_type} Responding: **{success_count}**\n"
#             f"NPCs Unable to Muster: **{fail_count}**\n"
#             f"NPCs with No Units: **{no_units_count}**"
#         )
#         embed.add_field(name="Muster Summary", value=summary_value, inline=False)
#         embed.add_field(
#             name="Context",
#             value=f"[Jump to Command]({ctx.message.jump_url})",
#             inline=False,
#         )
#         embed.set_footer(text=f"Liege House ID: {liege_player.house.house_id}")

#         try:
#             await channel.send(embed=embed)
#         except discord.Forbidden:
#             print(f"GM Alert failed: Bot lacks permissions in #{channel.name}")
#         except Exception as e:
#             print(f"An unexpected error occurred sending GM alert: {e}")

#     @commands.Cog.listener()
#     async def on_ready(self):
#         """This is crucial for making the views persistent."""
#         print("Checking for pending banner calls...")
#         async with get_session() as session:
#             stmt = select(PendingBannerCall).where(
#                 PendingBannerCall.status == "PENDING_APPROVAL"
#             )
#             pending_calls = (await session.execute(stmt)).scalars().all()
#             for call in pending_calls:
#                 # Re-register the view with the bot
#                 self.bot.add_view(
#                     BannerControlView(call.id), message_id=call.gm_message_id
#                 )
#             print(f"Re-initialized {len(pending_calls)} pending banner control panels.")

#     # @commands.command(name="call_banners")
#     # @commands.cooldown(1, 60, commands.BucketType.user)
#     # @commands.check(is_in_house_channel)
#     # async def call_banners(self, ctx, *, rally_point: str):
#     #     """Initiates a banner call. Notifies players and creates GM ticket for NPCs."""
#     #     player_wait_msg = await ctx.send("🦅 **Preparing banner call...**")

#     #     async with get_session() as session:
#     #         game = await GameRepo.get_active_game(session, ctx.guild.id)
#     #         if not game:
#     #             return await player_wait_msg.edit(content="❌ No active game.")

#     #         # Identify the Liege (Author)
#     #         liege_user_id = ctx.author.id

#     #         # --- SERVICE CALL ---
#     #         service = DiplomacyService(session)
#     #         success, npc_data, player_vassals = await service.prepare_banner_call(
#     #             game.game_id, liege_user_id
#     #         )

#     #         if not success:
#     #             return await player_wait_msg.edit(
#     #                 content="❌ Could not prepare call. Ensure you are the head of a house."
#     #             )

#     #         # If NOBODY is found (no players, no NPCs)
#     #         if not npc_data and not player_vassals:
#     #             return await player_wait_msg.edit(
#     #                 content="❌ You have no vassals to call."
#     #             )

#     #         # --- 1. NOTIFY PLAYER VASSALS ---
#     #         sent_count = 0
#     #         liege_name = "Your Liege"  # Placeholder

#     #         # Fetch liege name for the embed
#     #         stmt_l = (
#     #             select(GamePlayer)
#     #             .join(User)
#     #             .where(
#     #                 User.discord_id == liege_user_id, GamePlayer.game_id == game.game_id
#     #             )
#     #             .options(selectinload(GamePlayer.house))
#     #         )
#     #         liege_p = (await session.execute(stmt_l)).scalars().first()
#     #         if liege_p and liege_p.house:
#     #             liege_name = f"House {liege_p.house.name}"

#     #         if player_vassals:
#     #             for pv in player_vassals:
#     #                 # Construct channel name
#     #                 chan_name = f"{pv['house_name'].lower().replace(' ', '-')}-quarters"
#     #                 vassal_channel = discord.utils.get(
#     #                     ctx.guild.text_channels, name=chan_name
#     #                 )

#     #                 embed = discord.Embed(
#     #                     title="🦅 A Call to Arms!",
#     #                     description=f"**{liege_name}** has called the banners!\n\n"
#     #                     f"My Lord of **{pv['house_name']}**, your liege summons your forces to rally at **{rally_point}**.",
#     #                     color=discord.Color.dark_red(),
#     #                 )
#     #                 embed.add_field(
#     #                     name="Instructions",
#     #                     value=f"Muster your troops and use `!march` to proceed to **{rally_point}**.",
#     #                 )
#     #                 embed.set_thumbnail(url="https://img.icons8.com/color/96/war.png")

#     #                 try:
#     #                     # Try Channel
#     #                     if vassal_channel:
#     #                         await vassal_channel.send(
#     #                             f"<@{pv['user_id']}>", embed=embed
#     #                         )
#     #                         sent_count += 1
#     #                     else:
#     #                         # Try DM (using fetch to guarantee finding user)
#     #                         member = await ctx.guild.fetch_member(pv["user_id"])
#     #                         if member:
#     #                             await member.send(
#     #                                 f"⚠️ **Banner Call:** Your house channel was not found.",
#     #                                 embed=embed,
#     #                             )
#     #                             sent_count += 1
#     #                 except Exception as e:
#     #                     print(f"Failed to notify {pv['house_name']}: {e}")
#     #                     continue

#     #         # --- 2. HANDLE NPC VASSALS (GM Panel) ---
#     #         gm_msg_part = ""

#     #         if npc_data:
#     #             gm_channel = discord.utils.get(
#     #                 ctx.guild.text_channels, name="gm-alerts"
#     #             )
#     #             if not gm_channel:
#     #                 return await ctx.send("❌ Error: #gm-alerts channel missing.")

#     #             # Prepare DB data
#     #             vassal_data_for_db = [
#     #                 {
#     #                     "house_id": v["house"].house_id,
#     #                     "house_name": v["house"].name,
#     #                     "max_troops": v["troops"],
#     #                     "percent": v["percent"],
#     #                 }
#     #                 for v in npc_data
#     #             ]

#     #             new_pending_call = PendingBannerCall(
#     #                 game_id=game.game_id,
#     #                 guild_id=ctx.guild.id,
#     #                 channel_id=ctx.channel.id,
#     #                 message_id=player_wait_msg.id,
#     #                 gm_channel_id=gm_channel.id,
#     #                 gm_message_id=0,
#     #                 liege_house_id=liege_p.house.house_id,
#     #                 rally_point_name=rally_point,
#     #                 vassal_data=vassal_data_for_db,
#     #             )
#     #             session.add(new_pending_call)
#     #             await session.flush()

#     #             view = BannerControlView(new_pending_call.id)
#     #             embed = await view.create_embed(pending_call=new_pending_call)
#     #             gm_panel_msg = await gm_channel.send(embed=embed, view=view)

#     #             new_pending_call.gm_message_id = gm_panel_msg.id
#     #             await session.commit()

#     #             gm_msg_part = " NPC levies have been requested from the GMs."

#     #         # --- FINAL REPLY ---
#     #         await player_wait_msg.edit(
#     #             content=f"✅ **Call Sent!** Ravens dispatched to {sent_count} player vassals.{gm_msg_part} Rally Point: **{rally_point}**."
#     #         )

#     # @commands.command(name="call_banners")
#     # @commands.cooldown(1, 360, commands.BucketType.user)  # 6 Minutes
#     # @commands.check(is_in_house_channel)
#     # async def call_banners(self, ctx, *, rally_point: str):
#     #     """Initiates a banner call. Notifies players and creates GM ticket for NPCs."""
#     #     player_wait_msg = await ctx.send("🦅 **Preparing banner call...**")

#     #     async with get_session() as session:
#     #         game = await GameRepo.get_active_game(session, ctx.guild.id)
#     #         if not game:
#     #             return await player_wait_msg.edit(content="❌ No active game.")

#     #         # --- 1. GET PLAYER & HOUSE FIRST (Moved Up) ---
#     #         stmt_l = (
#     #             select(GamePlayer)
#     #             .join(User)
#     #             .where(
#     #                 User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#     #             )
#     #             .options(selectinload(GamePlayer.house))
#     #         )
#     #         liege_p = (await session.execute(stmt_l)).scalars().first()

#     #         # Validation: Do they even exist/have a house?
#     #         if not liege_p or not liege_p.claimed_house_id:
#     #             return await player_wait_msg.edit(
#     #                 content="❌ You do not command a house."
#     #             )

#     #         # --- 2. SPAM CHECK (Simplified) ---
#     #         # Now we can use 'liege_p.claimed_house_id' directly! No subquery needed.
#     #         stmt_check = select(PendingBannerCall).where(
#     #             PendingBannerCall.game_id == game.game_id,
#     #             PendingBannerCall.liege_house_id == liege_p.claimed_house_id,
#     #             PendingBannerCall.status == "PENDING_APPROVAL",
#     #         )
#     #         existing_call = (await session.execute(stmt_check)).scalars().first()

#     #         if existing_call:
#     #             return await player_wait_msg.edit(
#     #                 content=f"❌ **Hold:** You already have a banner call pending approval (ID: {existing_call.id}). Please wait for the GMs to resolve it."
#     #             )

#     #         # --- 3. SERVICE CALL ---
#     #         service = DiplomacyService(session)
#     #         success, npc_data, player_vassals = await service.prepare_banner_call(
#     #             game.game_id, ctx.author.id
#     #         )

#     #         if not success:
#     #             return await player_wait_msg.edit(
#     #                 content="❌ Could not prepare call. Ensure you are the head of a house."
#     #             )

#     #         if not npc_data and not player_vassals:
#     #             return await player_wait_msg.edit(
#     #                 content="❌ You have no vassals to call."
#     #             )

#     #         # --- 4. NOTIFY PLAYER VASSALS ---
#     #         sent_count = 0
#     #         liege_name = f"House {liege_p.house.name}"

#     #         if player_vassals:
#     #             for pv in player_vassals:
#     #                 chan_name = f"{pv['house_name'].lower().replace(' ', '-')}-quarters"
#     #                 vassal_channel = discord.utils.get(
#     #                     ctx.guild.text_channels, name=chan_name
#     #                 )

#     #                 embed = discord.Embed(
#     #                     title="🦅 A Call to Arms!",
#     #                     description=f"**{liege_name}** has called the banners!\n\n"
#     #                     f"My Lord of **{pv['house_name']}**, your liege summons your forces to rally at **{rally_point}**.",
#     #                     color=discord.Color.dark_red(),
#     #                 )
#     #                 embed.add_field(
#     #                     name="Instructions",
#     #                     value=f"Muster your troops and use `!march` to proceed to **{rally_point}**.",
#     #                 )
#     #                 embed.set_thumbnail(url="https://img.icons8.com/color/96/war.png")

#     #                 try:
#     #                     if vassal_channel:
#     #                         await vassal_channel.send(
#     #                             f"<@{pv['user_id']}>", embed=embed
#     #                         )
#     #                         sent_count += 1
#     #                     else:
#     #                         member = await ctx.guild.fetch_member(pv["user_id"])
#     #                         if member:
#     #                             await member.send(
#     #                                 f"⚠️ **Banner Call:** Your house channel was not found.",
#     #                                 embed=embed,
#     #                             )
#     #                             sent_count += 1
#     #                 except Exception as e:
#     #                     print(f"Failed to notify {pv['house_name']}: {e}")
#     #                     continue

#     #         # --- 5. HANDLE NPC VASSALS (GM Panel) ---
#     #         gm_msg_part = ""

#     #         if npc_data:
#     #             gm_channel = discord.utils.get(
#     #                 ctx.guild.text_channels, name="gm-alerts"
#     #             )
#     #             if not gm_channel:
#     #                 return await ctx.send("❌ Error: #gm-alerts channel missing.")

#     #             vassal_data_for_db = [
#     #                 {
#     #                     "house_id": v["house"].house_id,
#     #                     "house_name": v["house"].name,
#     #                     "max_troops": v["troops"],
#     #                     "percent": v["percent"],
#     #                 }
#     #                 for v in npc_data
#     #             ]

#     #             new_pending_call = PendingBannerCall(
#     #                 game_id=game.game_id,
#     #                 guild_id=ctx.guild.id,
#     #                 channel_id=ctx.channel.id,
#     #                 message_id=player_wait_msg.id,
#     #                 gm_channel_id=gm_channel.id,
#     #                 gm_message_id=0,
#     #                 liege_house_id=liege_p.house.house_id,
#     #                 rally_point_name=rally_point,
#     #                 vassal_data=vassal_data_for_db,
#     #             )
#     #             session.add(new_pending_call)
#     #             await session.flush()

#     #             view = BannerControlView(new_pending_call.id)
#     #             embed = await view.create_embed(pending_call=new_pending_call)
#     #             gm_panel_msg = await gm_channel.send(embed=embed, view=view)

#     #             new_pending_call.gm_message_id = gm_panel_msg.id
#     #             await session.commit()

#     #             gm_msg_part = " NPC levies have been requested from the GMs."

#     #         await player_wait_msg.edit(
#     #             content=f"✅ **Call Sent!** Ravens dispatched to {sent_count} player vassals.{gm_msg_part} Rally Point: **{rally_point}**."
#     #         )

#     @commands.command(name="call_banners")
#     @commands.cooldown(1, 360, commands.BucketType.user)  # 6 Minutes
#     @commands.check(is_in_house_channel)
#     async def call_banners(self, ctx, *, rally_point: str):
#         """Initiates a banner call. Notifies players and creates GM ticket for NPCs."""
#         player_wait_msg = await ctx.send("🦅 **Preparing banner call...**")

#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await player_wait_msg.edit(content="❌ No active game.")

#             # --- 1. GET PLAYER & HOUSE FIRST ---
#             stmt_l = (
#                 select(GamePlayer)
#                 .join(User)
#                 .where(
#                     User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#                 )
#                 .options(selectinload(GamePlayer.house))
#             )
#             liege_p = (await session.execute(stmt_l)).scalars().first()

#             # Validation
#             if not liege_p or not liege_p.claimed_house_id:
#                 return await player_wait_msg.edit(
#                     content="❌ You do not command a house."
#                 )

#             # --- 2. SPAM CHECK ---
#             stmt_check = select(PendingBannerCall).where(
#                 PendingBannerCall.game_id == game.game_id,
#                 PendingBannerCall.liege_house_id == liege_p.claimed_house_id,
#                 PendingBannerCall.status == "PENDING_APPROVAL",
#             )
#             existing_call = (await session.execute(stmt_check)).scalars().first()

#             if existing_call:
#                 return await player_wait_msg.edit(
#                     content=f"❌ **Hold:** You already have a banner call pending approval (ID: {existing_call.id}). Please wait for the GMs to resolve it."
#                 )

#             # --- 3. SERVICE CALL ---
#             service = DiplomacyService(session)
#             success, npc_data, player_vassals = await service.prepare_banner_call(
#                 game.game_id, ctx.author.id
#             )

#             if not success:
#                 return await player_wait_msg.edit(
#                     content="❌ Could not prepare call. Ensure you are the head of a house."
#                 )

#             if not npc_data and not player_vassals:
#                 return await player_wait_msg.edit(
#                     content="❌ You have no vassals to call."
#                 )

#             # --- 4. NOTIFY PLAYER VASSALS ---
#             sent_count = 0
#             liege_name = f"House {liege_p.house.name}"

#             if player_vassals:
#                 for pv in player_vassals:
#                     chan_name = f"{pv['house_name'].lower().replace(' ', '-')}-quarters"
#                     vassal_channel = discord.utils.get(
#                         ctx.guild.text_channels, name=chan_name
#                     )

#                     embed = discord.Embed(
#                         title="🦅 A Call to Arms!",
#                         description=f"**{liege_name}** has called the banners!\n\n"
#                         f"My Lord of **{pv['house_name']}**, your liege summons your forces to rally at **{rally_point}**.",
#                         color=discord.Color.dark_red(),
#                     )
#                     embed.add_field(
#                         name="Instructions",
#                         value=f"Muster your troops and use `!march` to proceed to **{rally_point}**.",
#                     )
#                     embed.set_thumbnail(url="https://img.icons8.com/color/96/war.png")

#                     try:
#                         if vassal_channel:
#                             await vassal_channel.send(
#                                 f"<@{pv['user_id']}>", embed=embed
#                             )
#                             sent_count += 1
#                         else:
#                             member = await ctx.guild.fetch_member(pv["user_id"])
#                             if member:
#                                 await member.send(
#                                     f"⚠️ **Banner Call:** Your house channel was not found.",
#                                     embed=embed,
#                                 )
#                                 sent_count += 1
#                     except Exception as e:
#                         print(f"Failed to notify {pv['house_name']}: {e}")
#                         continue

#             # --- 5. HANDLE NPC VASSALS (GM Panel) ---
#             gm_msg_part = ""

#             if npc_data:
#                 gm_channel = discord.utils.get(
#                     ctx.guild.text_channels, name="gm-alerts"
#                 )
#                 if not gm_channel:
#                     return await ctx.send("❌ Error: #gm-alerts channel missing.")

#                 # =========================================================
#                 # UPDATED MAPPER: CAPTURE TAG AND HOME COORDS
#                 # =========================================================
#                 vassal_data_for_db = [
#                     {
#                         "house_id": v["house"].house_id,
#                         "house_name": v["house"].name,
#                         "max_troops": v["troops"],
#                         "percent": v["percent"],
#                         # --- NEW FIELDS ---
#                         "tag": v.get("tag", ""),
#                         "home_x": v.get("home_x", 0),
#                         "home_y": v.get("home_y", 0),
#                     }
#                     for v in npc_data
#                 ]
#                 # =========================================================

#                 new_pending_call = PendingBannerCall(
#                     game_id=game.game_id,
#                     guild_id=ctx.guild.id,
#                     channel_id=ctx.channel.id,
#                     message_id=player_wait_msg.id,
#                     gm_channel_id=gm_channel.id,
#                     gm_message_id=0,
#                     liege_house_id=liege_p.house.house_id,
#                     rally_point_name=rally_point,
#                     vassal_data=vassal_data_for_db,
#                 )
#                 session.add(new_pending_call)
#                 await session.flush()

#                 view = BannerControlView(new_pending_call.id)
#                 embed = await view.create_embed(pending_call=new_pending_call)
#                 gm_panel_msg = await gm_channel.send(embed=embed, view=view)

#                 new_pending_call.gm_message_id = gm_panel_msg.id
#                 await session.commit()

#                 gm_msg_part = " NPC levies have been requested from the GMs."

#             await player_wait_msg.edit(
#                 content=f"✅ **Call Sent!** Ravens dispatched to {sent_count} player vassals.{gm_msg_part} Rally Point: **{rally_point}**."
#             )

#     @commands.Cog.listener()
#     async def on_interaction(self, interaction: discord.Interaction):
#         """
#         Listener for handling the persistent banner control buttons for both land and sea.
#         """
#         custom_id = interaction.data.get("custom_id")
#         if not custom_id or not custom_id.startswith("banner_"):
#             return

#         # --- Parse the custom ID safely ---
#         parts = custom_id.split("_")
#         if len(parts) < 3:
#             return  # Malformed ID

#         action, pending_call_id_str = parts[1], parts[-1]
#         try:
#             pending_call_id = int(pending_call_id_str)
#         except ValueError:
#             return  # Not a valid ID

#         # --- Fetch the pending call from the database ---
#         async with get_session() as session:
#             service = DiplomacyService(session)
#             pending_call = await session.get(PendingBannerCall, pending_call_id)

#             if not pending_call:
#                 await interaction.response.send_message(
#                     "❌ This banner call has expired or could not be found.",
#                     ephemeral=True,
#                 )
#                 return

#             # Prevent acting on already completed/cancelled calls
#             if pending_call.status != "PENDING_APPROVAL" and action in [
#                 "confirm",
#                 "cancel",
#             ]:
#                 await interaction.response.send_message(
#                     "❌ Action has already been taken on this banner call.",
#                     ephemeral=True,
#                 )
#                 return

#             # ===================================================
#             # ACTION: CONFIRM & MUSTER
#             # ===================================================
#             # if action == "confirm":
#             #     await interaction.response.defer()  # Acknowledge, as mustering can be slow

#             #     player_channel = self.bot.get_channel(pending_call.channel_id)
#             #     player_msg = None
#             #     if player_channel:
#             #         try:
#             #             player_msg = await player_channel.fetch_message(
#             #                 pending_call.message_id
#             #             )
#             #         except discord.NotFound:
#             #             print(
#             #                 f"Could not find original player message {pending_call.message_id} to update."
#             #             )

#             #     # --- Differentiate between LAND and SEA musters ---
#             #     if pending_call.call_type == "LAND":
#             #         march_results = await service.execute_muster_from_pending_call(
#             #             pending_call_id
#             #         )

#             #         if player_msg:
#             #             embeds = []
#             #             if march_results:
#             #                 for i in range(0, len(march_results), 10):
#             #                     chunk = march_results[i : i + 10]
#             #                     embed = discord.Embed(
#             #                         title="Banner Call Report: Levies Mustered!",
#             #                         description=f"Your vassals' levies are now marching on **{pending_call.rally_point_name}**.",
#             #                         color=discord.Color.green(),
#             #                     )
#             #                     embed.add_field(
#             #                         name="Levy Muster Status",
#             #                         value="\n".join(chunk),
#             #                         inline=False,
#             #                     )
#             #                     embeds.append(embed)
#             #             else:
#             #                 embeds.append(
#             #                     discord.Embed(
#             #                         title="Banner Call Report",
#             #                         description="No levies were mustered.",
#             #                         color=discord.Color.orange(),
#             #                     )
#             #                 )

#             #             paginator = Paginator(embeds)
#             #             await player_msg.edit(
#             #                 content="**GM Approved!** Your banner call has been executed.",
#             #                 embed=paginator.embeds[0],
#             #                 view=paginator,
#             #             )

#             #     elif pending_call.call_type == "SEA":
#             #         sail_results = await service.execute_sea_muster_from_pending_call(
#             #             pending_call_id
#             #         )

#             #         if player_msg:
#             #             embeds = []
#             #             if sail_results:
#             #                 for i in range(0, len(sail_results), 10):
#             #                     chunk = sail_results[i : i + 10]
#             #                     embed = discord.Embed(
#             #                         title="🌊 Fleet Muster Report: Fleets Mustered!",
#             #                         description=f"Your vassal fleets are setting sail for **{pending_call.rally_point_name}**.",
#             #                         color=discord.Color.green(),
#             #                     )
#             #                     embed.add_field(
#             #                         name="Fleet Status",
#             #                         value="\n".join(chunk),
#             #                         inline=False,
#             #                     )
#             #                     embeds.append(embed)
#             #             else:
#             #                 embeds.append(
#             #                     discord.Embed(
#             #                         title="Fleet Muster Report",
#             #                         description="No fleets were mustered.",
#             #                         color=discord.Color.orange(),
#             #                     )
#             #                 )

#             #             paginator = Paginator(embeds)
#             #             await player_msg.edit(
#             #                 content="**GM Approved!** Your naval levy call has been executed.",
#             #                 embed=paginator.embeds[0],
#             #                 view=paginator,
#             #             )

#             #     # --- Update the GM panel to show it's completed ---
#             #     view = BannerControlView(pending_call.id)
#             #     await interaction.edit_original_response(
#             #         embed=await view.create_embed(), view=None
#             #     )  # view=None removes buttons

#             if action == "confirm":
#                 # 1. Defer the interaction so it doesn't time out.
#                 await interaction.response.defer()

#                 # 2. IMMEDIATE FEEDBACK: Edit the original message to show a "processing" state.
#                 #    This removes the buttons and lets the GM know the command is running.
#                 await interaction.edit_original_response(
#                     content="⏳ **Processing...** Mustering the levies. This panel will update again upon completion.",
#                     view=None,  # Remove buttons to prevent double-clicks
#                 )

#                 # --- Find the player's message to update later ---
#                 player_channel = self.bot.get_channel(pending_call.channel_id)
#                 player_msg = None
#                 if player_channel:
#                     try:
#                         player_msg = await player_channel.fetch_message(
#                             pending_call.message_id
#                         )
#                     except discord.NotFound:
#                         print(
#                             f"Could not find original player message {pending_call.message_id} to update."
#                         )

#                 # 3. RUN THE LONG-RUNNING TASK (Pathfinding, DB updates, etc.)
#                 if pending_call.call_type == "LAND":
#                     march_results = await service.execute_muster_from_pending_call(
#                         pending_call_id
#                     )
#                     # Update the player's original message with the results
#                     if player_msg:
#                         embeds = []
#                         if march_results:
#                             for i in range(0, len(march_results), 10):
#                                 chunk = march_results[i : i + 10]
#                                 embed = discord.Embed(
#                                     title="Banner Call Report: Levies Mustered!",
#                                     description=f"Your vassals' levies are now marching on **{pending_call.rally_point_name}**.",
#                                     color=discord.Color.green(),
#                                 )
#                                 embed.add_field(
#                                     name="Levy Muster Status",
#                                     value="\n".join(chunk),
#                                     inline=False,
#                                 )
#                                 embeds.append(embed)
#                         else:
#                             embeds.append(
#                                 discord.Embed(
#                                     title="Banner Call Report",
#                                     description="No levies were mustered.",
#                                     color=discord.Color.orange(),
#                                 )
#                             )
#                         paginator = Paginator(embeds)
#                         await player_msg.edit(
#                             content="**GM Approved!** Your banner call has been executed.",
#                             embed=paginator.embeds[0],
#                             view=paginator,
#                         )

#                 elif pending_call.call_type == "SEA":
#                     sail_results = await service.execute_sea_muster_from_pending_call(
#                         pending_call_id
#                     )
#                     # Update the player's original message with the results
#                     if player_msg:
#                         embeds = []
#                         if sail_results:
#                             for i in range(0, len(sail_results), 10):
#                                 chunk = sail_results[i : i + 10]
#                                 embed = discord.Embed(
#                                     title="🌊 Fleet Muster Report: Fleets Mustered!",
#                                     description=f"Your vassal fleets are setting sail for **{pending_call.rally_point_name}**.",
#                                     color=discord.Color.green(),
#                                 )
#                                 embed.add_field(
#                                     name="Fleet Status",
#                                     value="\n".join(chunk),
#                                     inline=False,
#                                 )
#                                 embeds.append(embed)
#                         else:
#                             embeds.append(
#                                 discord.Embed(
#                                     title="Fleet Muster Report",
#                                     description="No fleets were mustered.",
#                                     color=discord.Color.orange(),
#                                 )
#                             )
#                         paginator = Paginator(embeds)
#                         await player_msg.edit(
#                             content="**GM Approved!** Your naval levy call has been executed.",
#                             embed=paginator.embeds[0],
#                             view=paginator,
#                         )

#                 # 4. FINAL UPDATE: Now that the work is done, edit the GM panel again
#                 #    to show the final "Completed" status.
#                 view = BannerControlView(pending_call.id)
#                 await interaction.edit_original_response(
#                     content="✅ **Muster Complete!** Report sent to the player.",
#                     embed=await view.create_embed(),  # This will now show the 'COMPLETED' status
#                     view=None,
#                 )

#             # ===================================================
#             # ACTION: CANCEL
#             # ===================================================
#             elif action == "cancel":
#                 pending_call.status = "CANCELLED"
#                 await session.commit()

#                 # Notify player
#                 player_channel = self.bot.get_channel(pending_call.channel_id)
#                 if player_channel:
#                     try:
#                         player_msg = await player_channel.fetch_message(
#                             pending_call.message_id
#                         )
#                         await player_msg.edit(
#                             content=f"❌ **A GM has cancelled your banner call for {pending_call.rally_point_name}.**",
#                             embed=None,
#                             view=None,
#                         )
#                     except discord.NotFound:
#                         pass  # Message was deleted, nothing to do

#                 # Update GM panel
#                 view = BannerControlView(pending_call.id)
#                 await interaction.response.edit_message(
#                     embed=await view.create_embed(), view=None
#                 )

#             # ===================================================
#             # ACTION: HELP
#             # ===================================================
#             elif action == "help":
#                 unit_name = "ships" if pending_call.call_type == "SEA" else "troops"
#                 help_text = (
#                     "**How to use this panel:**\n"
#                     f"1. **Review:** See the default levy contributions ({unit_name}).\n"
#                     f"2. **Adjust:** Use the dropdown menu to select a vassal. A pop-up will ask for the new percentage (0-100) of {unit_name} you want them to contribute.\n"
#                     "3. **Confirm:** Once all percentages are correct, click `Confirm & Muster Levies`. This is the final step and cannot be undone.\n"
#                     "4. **Cancel:** If the banner call should not proceed, click `Cancel Banner Call`."
#                 )
#                 await interaction.response.send_message(help_text, ephemeral=True)

#             elif action == "adjust":
#                 # Import here to avoid circular imports at top level if necessary
#                 from app.ui.banner_view import AdjustVassalModal

#                 modal = AdjustVassalModal(pending_call.id, pending_call.call_type)
#                 await interaction.response.send_modal(modal)

#             # Break the session loop after handling the interaction

#     # @commands.command(name="call_levies_sea")
#     # @commands.check(is_in_house_channel)
#     # async def call_levies_sea(self, ctx, *, rally_point: str):
#     #     """Initiates a naval levy call, pending GM approval."""
#     #     player_wait_msg = await ctx.send(
#     #         "🌊 **Preparing naval levy call... This will require GM approval.**"
#     #     )

#     #     async with get_session() as session:
#     #         game = await GameRepo.get_active_game(session, ctx.guild.id)
#     #         if not game:
#     #             return await player_wait_msg.edit(
#     #                 content="❌ No active game found for this server."
#     #             )

#     #         stmt_p = (
#     #             select(GamePlayer)
#     #             .join(User, GamePlayer.user_id == User.user_id)
#     #             .where(
#     #                 User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#     #             )
#     #             .options(selectinload(GamePlayer.house))
#     #         )
#     #         liege_player = (await session.execute(stmt_p)).scalars().first()
#     #         if not liege_player or not liege_player.house:
#     #             return await player_wait_msg.edit(
#     #                 content="❌ Could not find a house for you in the active game."
#     #             )

#     #         service = DiplomacyService(session)
#     #         success, vassal_data, player_vassals = await service.prepare_sea_levy_call(
#     #             game.game_id, ctx.author.id
#     #         )

#     #         if not success or not vassal_data:
#     #             return await player_wait_msg.edit(
#     #                 content="❌ Could not prepare the naval levy call. No valid NPC vassals with fleets found."
#     #             )

#     #         if player_vassals:
#     #             notified_count = 0
#     #             for pv in player_vassals:
#     #                 chan_name = f"{pv['house_name'].lower().replace(' ', '-')}-quarters"
#     #                 channel = discord.utils.get(ctx.guild.text_channels, name=chan_name)
#     #                 embed = discord.Embed(
#     #                     title="🌊 A Call for Fleets!", color=discord.Color.blue()
#     #                 )
#     #                 embed.description = f"**{ctx.author.display_name}** calls the fleets to rally at **{rally_point}**!"
#     #                 embed.add_field(
#     #                     name="Orders",
#     #                     value="Report the status of your fleet and set sail immediately.",
#     #                     inline=False,
#     #                 )
#     #                 embed.set_footer(
#     #                     text="To respond, use !sail or contact your Liege."
#     #                 )
#     #                 if channel:
#     #                     await channel.send(f"<@{pv['discord_id']}>", embed=embed)
#     #                     notified_count += 1
#     #             await ctx.send(
#     #                 f"📨 **Messages Sent:** Notified {notified_count} player vassals with ports."
#     #             )

#     #         gm_channel = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")
#     #         if not gm_channel:
#     #             return await player_wait_msg.edit(
#     #                 content="❌ Critical error: #gm-alerts channel not found. Cannot proceed."
#     #             )

#     #         vassal_data_for_db = [
#     #             {
#     #                 "house_id": v["house"].house_id,
#     #                 "house_name": v["house"].name,
#     #                 "max_ships": v["ships"],
#     #                 "percent": v["percent"],
#     #                 "start_location": v["start_location"],
#     #             }
#     #             for v in vassal_data
#     #         ]

#     #         new_pending_call = PendingBannerCall(
#     #             game_id=game.game_id,
#     #             guild_id=ctx.guild.id,
#     #             channel_id=ctx.channel.id,
#     #             message_id=player_wait_msg.id,
#     #             gm_channel_id=gm_channel.id,
#     #             gm_message_id=0,
#     #             liege_house_id=liege_player.house.house_id,
#     #             rally_point_name=rally_point,
#     #             vassal_data=vassal_data_for_db,
#     #             call_type="SEA",  # Set the type for the listener
#     #         )
#     #         session.add(new_pending_call)
#     #         await session.flush()

#     #         view = BannerControlView(new_pending_call.id)
#     #         # gm_panel_msg = await gm_channel.send(
#     #         #     embed=await view.create_embed(), view=view
#     #         # )
#     #         embed = await view.create_embed(pending_call=new_pending_call)
#     #         gm_panel_msg = await gm_channel.send(embed=embed, view=view)

#     #         new_pending_call.gm_message_id = gm_panel_msg.id
#     #         await session.commit()

#     #         await player_wait_msg.edit(
#     #             content=f"✅ **Your naval levy call for {rally_point} has been sent to the GMs for approval and adjustment.** You will be notified when they muster the fleets."
#     #         )

#     @commands.command(name="call_levies_sea")
#     @commands.cooldown(1, 360, commands.BucketType.user)  # 1 use every 6 minutes
#     @commands.check(is_in_house_channel)
#     async def call_levies_sea(self, ctx, *, rally_point: str):
#         """Initiates a naval levy call, pending GM approval."""
#         player_wait_msg = await ctx.send(
#             "🌊 **Preparing naval levy call... This will require GM approval.**"
#         )

#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await player_wait_msg.edit(
#                     content="❌ No active game found for this server."
#                 )

#             # --- 1. GET PLAYER & HOUSE FIRST (Moved Up) ---
#             stmt_p = (
#                 select(GamePlayer)
#                 .join(User, GamePlayer.user_id == User.user_id)
#                 .where(
#                     User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#                 )
#                 .options(selectinload(GamePlayer.house))
#             )
#             liege_player = (await session.execute(stmt_p)).scalars().first()

#             if not liege_player or not liege_player.house:
#                 return await player_wait_msg.edit(
#                     content="❌ Could not find a house for you in the active game."
#                 )

#             # --- 2. SPAM CHECK (State Locking) ---
#             # Check if they already have a NAVAL call pending
#             stmt_check = select(PendingBannerCall).where(
#                 PendingBannerCall.game_id == game.game_id,
#                 PendingBannerCall.liege_house_id == liege_player.house.house_id,
#                 PendingBannerCall.status == "PENDING_APPROVAL",
#                 PendingBannerCall.call_type == "SEA",  # Specific check for Sea calls
#             )
#             existing_call = (await session.execute(stmt_check)).scalars().first()

#             if existing_call:
#                 return await player_wait_msg.edit(
#                     content=f"❌ **Hold:** You already have a naval levy call pending approval (ID: {existing_call.id}). Please wait for the GMs to resolve it."
#                 )

#             # --- 3. SERVICE CALL ---
#             service = DiplomacyService(session)
#             success, vassal_data, player_vassals = await service.prepare_sea_levy_call(
#                 game.game_id, ctx.author.id
#             )

#             if not success:
#                 return await player_wait_msg.edit(
#                     content="❌ Could not prepare the naval levy call. Ensure you are the head of a house."
#                 )

#             if not vassal_data and not player_vassals:
#                 return await player_wait_msg.edit(
#                     content="❌ You have no naval vassals (NPC or Player) to call."
#                 )

#             # --- 4. NOTIFY PLAYER VASSALS ---
#             if player_vassals:
#                 notified_count = 0
#                 for pv in player_vassals:
#                     chan_name = f"{pv['house_name'].lower().replace(' ', '-')}-quarters"
#                     channel = discord.utils.get(ctx.guild.text_channels, name=chan_name)

#                     embed = discord.Embed(
#                         title="🌊 A Call for Fleets!", color=discord.Color.blue()
#                     )
#                     embed.description = f"**{ctx.author.display_name}** calls the fleets to rally at **{rally_point}**!"
#                     embed.add_field(
#                         name="Orders",
#                         value="Report the status of your fleet and set sail immediately.",
#                         inline=False,
#                     )
#                     embed.set_footer(
#                         text="To respond, use !sail or contact your Liege."
#                     )

#                     try:
#                         if channel:
#                             await channel.send(f"<@{pv['discord_id']}>", embed=embed)
#                             notified_count += 1
#                         else:
#                             # Fallback to DM if channel missing
#                             member = await ctx.guild.fetch_member(pv["discord_id"])
#                             if member:
#                                 await member.send(
#                                     f"⚠️ **Naval Call:** House channel missing.",
#                                     embed=embed,
#                                 )
#                                 notified_count += 1
#                     except Exception as e:
#                         print(f"Failed to notify naval vassal {pv['house_name']}: {e}")

#                 await ctx.send(
#                     f"📨 **Messages Sent:** Notified {notified_count} player vassals with ports."
#                 )

#             # --- 5. HANDLE NPC VASSALS (GM Panel) ---
#             if not vassal_data:
#                 return await player_wait_msg.edit(
#                     content=f"✅ **Call Sent!** Notified players. No NPC fleets were found to muster."
#                 )

#             gm_channel = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")
#             if not gm_channel:
#                 return await player_wait_msg.edit(
#                     content="❌ Critical error: #gm-alerts channel not found. Cannot proceed with NPC muster."
#                 )

#             vassal_data_for_db = [
#                 {
#                     "house_id": v["house"].house_id,
#                     "house_name": v["house"].name,
#                     "max_ships": v["ships"],
#                     "percent": v["percent"],
#                     "start_location": v["start_location"],
#                 }
#                 for v in vassal_data
#             ]

#             new_pending_call = PendingBannerCall(
#                 game_id=game.game_id,
#                 guild_id=ctx.guild.id,
#                 channel_id=ctx.channel.id,
#                 message_id=player_wait_msg.id,
#                 gm_channel_id=gm_channel.id,
#                 gm_message_id=0,
#                 liege_house_id=liege_player.house.house_id,
#                 rally_point_name=rally_point,
#                 vassal_data=vassal_data_for_db,
#                 call_type="SEA",  # Set the type for the listener
#             )
#             session.add(new_pending_call)
#             await session.flush()

#             view = BannerControlView(new_pending_call.id)
#             embed = await view.create_embed(pending_call=new_pending_call)
#             gm_panel_msg = await gm_channel.send(embed=embed, view=view)

#             new_pending_call.gm_message_id = gm_panel_msg.id
#             await session.commit()

#             await player_wait_msg.edit(
#                 content=f"✅ **Your naval levy call for {rally_point} has been sent to the GMs for approval and adjustment.** You will be notified when they muster the fleets."
#             )

#     @commands.command(name="vassals")
#     @commands.check(is_in_house_channel)
#     async def list_vassals(self, ctx):
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             stmt = (
#                 select(GamePlayer)
#                 .join(User)
#                 .where(
#                     User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#                 )
#                 .options(selectinload(GamePlayer.house))
#             )
#             player = (await session.execute(stmt)).scalars().first()

#             if not player or not player.claimed_house_id:
#                 return await ctx.send("❌ No house.")

#             stmt_v = (
#                 select(House)
#                 .where(
#                     House.liege_id == player.claimed_house_id,
#                     House.game_id == game.game_id,
#                 )
#                 .options(selectinload(House.armies), selectinload(House.fiefs))
#             )
#             vassals = (await session.execute(stmt_v)).scalars().all()

#             if not vassals:
#                 return await ctx.send("🍂 No vassals.")

#             lines = []
#             for v in vassals:
#                 troops = sum(a.troop_count for a in v.armies)
#                 lines.append(f"**{v.name}** | 🏰 {len(v.fiefs)}")

#             chunks = [lines[i : i + 10] for i in range(0, len(lines), 10)]
#             embeds = [
#                 discord.Embed(
#                     title=f"Banners of {player.house.name}",
#                     description="\n".join(c),
#                     color=discord.Color.gold(),
#                 )
#                 for c in chunks
#             ]

#             if embeds:
#                 await ctx.send(
#                     embed=embeds[0], view=Paginator(embeds) if len(embeds) > 1 else None
#                 )

#     @commands.command(name="declare_fealty")
#     @commands.check(is_in_house_channel)
#     async def declare_fealty(self, ctx, *, new_liege: str):
#         """
#         Swear loyalty to a new House.
#         Usage: !declare_fealty Targaryen
#         """
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await ctx.send("❌ No active game.")

#             stmt = select(User).where(User.discord_id == ctx.author.id)
#             user = (await session.execute(stmt)).scalars().first()
#             if not user:
#                 return await ctx.send("❌ You are not in the game.")

#             service = DiplomacyService(session)
#             success, msg = await service.declare_fealty(
#                 game.game_id, user.user_id, new_liege
#             )

#             # Post to declarations channel
#             if success:
#                 dec_channel = discord.utils.get(
#                     ctx.guild.text_channels, name="declarations"
#                 )
#                 if dec_channel:
#                     embed = discord.Embed(
#                         title="📜 Declaration of Fealty",
#                         description=msg,
#                         color=discord.Color.blue(),
#                     )
#                     await dec_channel.send(embed=embed)

#             await ctx.send(msg)

#     @commands.command(name="declare_war")
#     @commands.check(is_in_house_channel)
#     async def declare_war(self, ctx, target: str, *, reason: str = "Aggression"):
#         """
#         Declare war on a House or Character.
#         """
#         dec_channel = discord.utils.get(ctx.guild.text_channels, name="declarations")
#         if dec_channel:
#             embed = discord.Embed(
#                 title="⚔️ Declaration of War", color=discord.Color.red()
#             )
#             embed.description = (
#                 f"**{ctx.author.display_name}** has declared WAR on **{target}**!"
#             )
#             embed.add_field(name="Casus Belli", value=reason)
#             await dec_channel.send(embed=embed)
#             await ctx.send("✅ War declared.")
#         else:
#             await ctx.send("❌ Declarations channel not found.")

#     @commands.command(name="disband_levies")
#     @commands.check(is_in_house_channel)
#     async def disband_levies(self, ctx):
#         """
#         Returns all NPC levies to their home castles.
#         """
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return

#             stmt = select(User).where(User.discord_id == ctx.author.id)
#             user = (await session.execute(stmt)).scalars().first()
#             if not user:
#                 return

#             service = DiplomacyService(session)
#             success, result = await service.disband_levies(game.game_id, user.user_id)

#             if success:
#                 if not result:
#                     await ctx.send("ℹ️ No active levies were found to disband.")
#                     return

#                 # --- PAGINATION LOGIC ---
#                 embeds = []
#                 chunk_size = 15  # Or however many lines fit comfortably
#                 for i in range(0, len(result), chunk_size):
#                     chunk = result[i : i + chunk_size]

#                     embed = discord.Embed(
#                         title="🏳️ Levies Disbanded",
#                         description="\n".join(chunk),
#                         color=discord.Color.blue(),
#                     )
#                     embeds.append(embed)

#                 paginator_view = Paginator(embeds)
#                 await ctx.send(embed=paginator_view.embeds[0], view=paginator_view)
#                 # --- END PAGINATION LOGIC ---
#             else:
#                 await ctx.send(result)

#     # @commands.command(name="meet")
#     # async def meet(
#     #     self,
#     #     ctx: commands.Context,
#     #     target: discord.Member,
#     #     *,
#     #     location: str = "A Private Setting",
#     # ):
#     #     """
#     #     Requests a private meeting with another player, creating a channel upon consent.
#     #     Usage: !meet @PlayerName location="The Wolf's Den"
#     #     """
#     #     if target.bot or target == ctx.author:
#     #         return await ctx.send("❌ You cannot meet with a bot or yourself.")

#     #     async with get_session() as session:
#     #         game = await GameRepo.get_active_game(session, ctx.guild.id)
#     #         if not game:
#     #             return await ctx.send("❌ No active game is running on this server.")

#     #         # --- Get Character/House names for RP Flavor ---
#     #         initiator_player = await session.scalar(
#     #             select(GamePlayer)
#     #             .join(User)
#     #             .where(
#     #                 User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#     #             )
#     #             .options(
#     #                 selectinload(GamePlayer.character), selectinload(GamePlayer.house)
#     #             )
#     #         )
#     #         target_player = await session.scalar(
#     #             select(GamePlayer)
#     #             .join(User)
#     #             .where(User.discord_id == target.id, GamePlayer.game_id == game.game_id)
#     #             .options(
#     #                 selectinload(GamePlayer.character), selectinload(GamePlayer.house)
#     #             )
#     #         )

#     #         if not initiator_player or not target_player:
#     #             return await ctx.send(
#     #                 "❌ One or both participants are not active players in the game."
#     #             )

#     #         # Use character name if available, otherwise fall back to house name, then display name
#     #         initiator_name = (
#     #             initiator_player.character.name
#     #             if initiator_player.character
#     #             else (
#     #                 initiator_player.house.name
#     #                 if initiator_player.house
#     #                 else ctx.author.display_name
#     #             )
#     #         )

#     #         target_name = (
#     #             target_player.character.name
#     #             if target_player.character
#     #             else (
#     #                 target_player.house.name
#     #                 if target_player.house
#     #                 else target.display_name
#     #             )
#     #         )

#     #         # --- The function that runs AFTER the target accepts ---
#     #         async def on_accept(interaction: discord.Interaction):
#     #             guild = interaction.guild

#     #             # 1. Get or create the "Meetings" category
#     #             category = discord.utils.get(guild.categories, name="Meetings")
#     #             if not category:
#     #                 try:
#     #                     category = await guild.create_category("Meetings")
#     #                 except discord.Forbidden:
#     #                     await interaction.followup.send(
#     #                         "❌ I don't have permission to create categories.",
#     #                         ephemeral=True,
#     #                     )
#     #                     return

#     #             # 2. Sanitize names and create a unique channel name
#     #             sanitized_initiator = re.sub(
#     #                 r"[^a-zA-Z0-9-]", "", initiator_name.lower().replace(" ", "-")
#     #             )
#     #             sanitized_target = re.sub(
#     #                 r"[^a-zA-Z0-9-]", "", target_name.lower().replace(" ", "-")
#     #             )
#     #             channel_name = (
#     #                 f"meet-{sanitized_initiator[:15]}-{sanitized_target[:15]}"
#     #             )

#     #             # 3. Define channel permissions
#     #             overwrites = {
#     #                 guild.default_role: discord.PermissionOverwrite(
#     #                     read_messages=False
#     #                 ),
#     #                 ctx.author: discord.PermissionOverwrite(
#     #                     read_messages=True, send_messages=True
#     #                 ),
#     #                 target: discord.PermissionOverwrite(
#     #                     read_messages=True, send_messages=True
#     #                 ),
#     #                 guild.me: discord.PermissionOverwrite(
#     #                     read_messages=True, send_messages=True
#     #                 ),  # Bot needs perms too
#     #             }

#     #             # 4. Create the private channel
#     #             try:
#     #                 channel = await guild.create_text_channel(
#     #                     name=channel_name,
#     #                     category=category,
#     #                     overwrites=overwrites,
#     #                     topic=f"A private meeting between {initiator_name} and {target_name}.",
#     #                 )
#     #             except discord.Forbidden:
#     #                 await interaction.followup.send(
#     #                     "❌ I don't have permission to create channels.", ephemeral=True
#     #                 )
#     #                 return

#     #             # 5. Send a welcome message in the new channel
#     #             welcome_embed = discord.Embed(
#     #                 title="Meeting Room",
#     #                 description=f"This is a private channel for **{initiator_name}** and **{target_name}**.",
#     #                 color=discord.Color.dark_grey(),
#     #             )
#     #             welcome_embed.add_field(name="📍 Location", value=location)
#     #             await channel.send(
#     #                 f"The meeting may now begin. {ctx.author.mention} {target.mention}",
#     #                 embed=welcome_embed,
#     #             )

#     #             # 6. Give feedback to the user who accepted
#     #             await interaction.followup.send(
#     #                 f"✅ The private meeting room has been created: {channel.mention}",
#     #                 ephemeral=True,
#     #             )

#     #         # --- Create and send the proposal ---
#     #         proposal_embed = discord.Embed(
#     #             title="📜 A Request for Audience",
#     #             description=f"**{initiator_name}** formally requests a private meeting with **{target_name}**.",
#     #             color=discord.Color.blurple(),
#     #         )

#     #         view = ProposalView(
#     #             initiator=ctx.author,
#     #             consenter=target,
#     #             action_name="Meeting",
#     #             proposal_embed=proposal_embed,
#     #             on_accept_callback=on_accept,
#     #         )

#     #         await ctx.send(
#     #             f"{target.mention}, you have received a proposal.",
#     #             embed=proposal_embed,
#     #             view=view,
#     #         )

#     async def _create_meeting_channel(
#         self,
#         guild: discord.Guild,
#         member1: discord.Member,
#         member2: discord.Member,
#         location_str: str,
#         name1: str,
#         name2: str,
#     ) -> discord.TextChannel | None:
#         """A generic helper to create a private meeting channel for two members."""

#         # 1. Get or create the "Meetings" category
#         category = discord.utils.get(guild.categories, name="Meetings")
#         if not category:
#             try:
#                 category = await guild.create_category("Meetings")
#             except discord.Forbidden:
#                 print("ERROR: Bot lacks permission to create categories.")
#                 return None

#         # 2. Sanitize names and create a unique channel name
#         sanitized_name1 = re.sub(r"[^a-zA-Z0-9-]", "", name1.lower().replace(" ", "-"))
#         sanitized_name2 = re.sub(r"[^a-zA-Z0-9-]", "", name2.lower().replace(" ", "-"))
#         channel_name = f"meet-{sanitized_name1[:15]}-{sanitized_name2[:15]}"

#         # 3. Define channel permissions
#         overwrites = {
#             guild.default_role: discord.PermissionOverwrite(read_messages=False),
#             member1: discord.PermissionOverwrite(
#                 read_messages=True, send_messages=True
#             ),
#             member2: discord.PermissionOverwrite(
#                 read_messages=True, send_messages=True
#             ),
#             guild.me: discord.PermissionOverwrite(
#                 read_messages=True, send_messages=True
#             ),
#         }

#         # 4. Create the private channel
#         try:
#             channel = await guild.create_text_channel(
#                 name=channel_name,
#                 category=category,
#                 overwrites=overwrites,
#                 topic=f"A private meeting between {name1} and {name2}.",
#             )
#         except discord.Forbidden:
#             print(f"ERROR: Bot lacks permission to create channel '{channel_name}'.")
#             return None

#         # 5. Send a welcome message in the new channel
#         welcome_embed = discord.Embed(
#             title="Meeting Room",
#             description=f"This is a private channel for **{name1}** and **{name2}**.",
#             color=discord.Color.dark_grey(),
#         )
#         welcome_embed.add_field(name="📍 Location", value=location_str)
#         await channel.send(
#             f"The meeting may now begin. {member1.mention} {member2.mention}",
#             embed=welcome_embed,
#         )

#         return channel

#     @commands.command(name="meet")
#     async def meet(
#         self,
#         ctx: commands.Context,
#         target: discord.Member,
#         *,
#         location: str = "A Private Setting",
#     ):
#         """
#         Requests a private meeting with another player, creating a channel upon consent.
#         Usage: !meet @PlayerName location="The Wolf's Den"
#         """
#         if target.bot or target == ctx.author:
#             return await ctx.send("❌ You cannot meet with a bot or yourself.")

#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await ctx.send("❌ No active game is running on this server.")

#             # --- Get Character/House names for RP Flavor ---
#             initiator_player = await session.scalar(
#                 select(GamePlayer)
#                 .join(User)
#                 .where(
#                     User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
#                 )
#                 .options(
#                     selectinload(GamePlayer.character), selectinload(GamePlayer.house)
#                 )
#             )
#             target_player = await session.scalar(
#                 select(GamePlayer)
#                 .join(User)
#                 .where(User.discord_id == target.id, GamePlayer.game_id == game.game_id)
#                 .options(
#                     selectinload(GamePlayer.character), selectinload(GamePlayer.house)
#                 )
#             )

#             if not initiator_player or not target_player:
#                 return await ctx.send(
#                     "❌ One or both participants are not active players in the game."
#                 )

#             initiator_name = (
#                 initiator_player.character.name
#                 if initiator_player.character
#                 else (
#                     initiator_player.house.name
#                     if initiator_player.house
#                     else ctx.author.display_name
#                 )
#             )
#             target_name = (
#                 target_player.character.name
#                 if target_player.character
#                 else (
#                     target_player.house.name
#                     if target_player.house
#                     else target.display_name
#                 )
#             )

#             # --- The function that runs AFTER the target accepts (NOW REFACTORED) ---
#             async def on_accept(interaction: discord.Interaction):
#                 # This function is now much simpler. It just calls our reusable helper.
#                 channel = await self._create_meeting_channel(
#                     guild=interaction.guild,
#                     member1=ctx.author,
#                     member2=target,
#                     location_str=location,
#                     name1=initiator_name,
#                     name2=target_name,
#                 )

#                 # Give feedback to the user who accepted the proposal
#                 if channel:
#                     await interaction.followup.send(
#                         f"✅ The private meeting room has been created: {channel.mention}",
#                         ephemeral=True,
#                     )
#                 else:
#                     await interaction.followup.send(
#                         "❌ Failed to create the meeting room. Please check the bot's permissions.",
#                         ephemeral=True,
#                     )

#             # --- Create and send the proposal (No changes here) ---
#             proposal_embed = discord.Embed(
#                 title="📜 A Request for Audience",
#                 description=f"**{initiator_name}** formally requests a private meeting with **{target_name}**.",
#                 color=discord.Color.blurple(),
#             )

#             # Assuming you have a generic ProposalView for this
#             from app.ui.social_views import ProposalView

#             view = ProposalView(
#                 initiator=ctx.author,
#                 consenter=target,
#                 action_name="Meeting",
#                 proposal_embed=proposal_embed,
#                 on_accept_callback=on_accept,
#             )

#             await ctx.send(
#                 f"{target.mention}, you have received a proposal.",
#                 embed=proposal_embed,
#                 view=view,
#             )

#     @commands.command(name="marry")
#     async def marry(self, ctx, *, query: str):
#         """
#         Arrange a marriage between two characters. Requires consent.
#         Usage: !marry "[Character A]" to "[Character B]"
#         """
#         await self._handle_union_proposal(ctx, query, action_name="Marriage", icon="💍")

#     @commands.command(name="betroth")
#     async def betroth(self, ctx, *, query: str):
#         """
#         Arrange a betrothal between two characters. Requires consent.
#         Usage: !betroth "[Character A]" to "[Character B]"
#         """
#         await self._handle_union_proposal(
#             ctx, query, action_name="Betrothal", icon="📜"
#         )


# async def setup(bot):
#     await bot.add_cog(DiplomacyCog(bot))


# UNCOMMENT ABOVE

import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import re
import datetime
from typing import List, Literal

from app.db.db_manager import get_session
from app.db.models import User, GamePlayer, House, Character, PendingBannerCall
from app.db.repositories import GameRepo
from app.services.diplomacy_service import DiplomacyService

# from app.ui.banner_view import BannerControlView
from app.ui.paginator import Paginator
from app.ui.social_views import ProposalView
from app.checks import is_in_house_channel  # Assuming is_in_house_channel is defined

from app.ui.banner_view import BannerControlView


# --- Custom GM Check (Needs to be defined outside the Cog or as a static method/helper) ---
async def is_gm(ctx):
    async with get_session() as session:
        # Assuming User model has an `is_gm` boolean field
        user = await session.scalar(
            select(User).where(User.discord_id == ctx.author.id)
        )
        if user and user.is_gm:
            return True
        return False


class DiplomacyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _handle_union_proposal(
        self, ctx: commands.Context, query: str, action_name: str, icon: str
    ):
        """A shared helper to process both marriage and betrothal proposals."""
        parts = re.split(r"\s+to\s+", query, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2:
            return await ctx.send(
                f'❌ Format: `!{ctx.invoked_with} "[Person A]" to "[Person B]"`'
            )

        char_a_name, char_b_name = parts[0].strip("\"' "), parts[1].strip("\"' ")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            service = DiplomacyService(session)

            arranger_player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )
            if not arranger_player:
                return await ctx.send("❌ You are not a player in this game.")

            char_a = await service.find_or_create_char(game.game_id, char_a_name)
            char_b = await service.find_or_create_char(game.game_id, char_b_name)
            if not char_a or not char_b:
                return await ctx.send(
                    "❌ One or more characters/houses could not be found."
                )
            if char_a.spouse_id or char_b.spouse_id:
                return await ctx.send("❌ One of the characters is already married.")

            if not await service.check_marriage_authority(arranger_player, char_a):
                return await ctx.send(
                    f"❌ You do not have the authority to arrange a {action_name.lower()} for **{char_a.name}**."
                )

            consenting_player_obj = await service.find_consenting_player(char_b)

            async def on_accept(interaction: discord.Interaction):
                success, msg = await service.execute_marriage(
                    game.game_id, char_a.name, char_b.name
                )
                news_channel = discord.utils.get(
                    interaction.guild.text_channels, name="marriages"
                )
                if success and news_channel:
                    await news_channel.send(f"{icon} {msg}")

                final_embed = interaction.message.embeds[0]
                final_embed.set_footer(text=f"✅ {action_name} Confirmed!")
                final_embed.color = discord.Color.green()
                await interaction.edit_original_message(embed=final_embed, view=None)

            # Case A: GM Approval needed
            if not consenting_player_obj:
                gm_channel = discord.utils.get(
                    ctx.guild.text_channels, name="gm-alerts"
                )
                if not gm_channel:
                    return await ctx.send("❌ GM alerts channel not found.")
                proposal_embed = discord.Embed(
                    title=f"{icon} GM Approval: {action_name}",
                    description=f"**{ctx.author.display_name}** requests a union between **{char_a.name}** and the NPC **{char_b.name}**.",
                    color=discord.Color.dark_purple(),
                )
                view = ProposalView(
                    initiator=ctx.author,
                    consenter=ctx.author,
                    action_name=action_name,
                    proposal_embed=proposal_embed,
                    on_accept_callback=on_accept,
                    is_gm_approval=True,
                )
                await gm_channel.send(embed=proposal_embed, view=view)
                return await ctx.send(
                    "✅ Your proposal has been sent to the GMs for approval."
                )

            # Case B: Arranger has authority over both (Auto-Accept)
            elif consenting_player_obj.user.discord_id == ctx.author.id:
                success, msg = await service.execute_marriage(
                    game.game_id, char_a.name, char_b.name
                )
                news_channel = discord.utils.get(
                    ctx.guild.text_channels, name="marriages"
                )
                if success and news_channel:
                    await news_channel.send(f"{icon} {msg}")
                return await ctx.send(msg)

            # Case C: Another player must consent
            else:
                try:
                    consenter_member = await ctx.guild.fetch_member(
                        consenting_player_obj.user.discord_id
                    )
                except discord.NotFound:
                    consenter_member = None

                if not consenter_member:
                    consenter_name = (
                        consenting_player_obj.character.name
                        if consenting_player_obj.character
                        else (
                            consenting_player_obj.house.name
                            if consenting_player_obj.house
                            else f"User ID {consenting_player_obj.user.discord_id}"
                        )
                    )
                    return await ctx.send(
                        f"❌ The required consenter for this union ({consenter_name}) could not be found in this server."
                    )

                proposal_embed = discord.Embed(
                    title=f"{icon} {action_name} Proposal",
                    description=f"**{ctx.author.display_name}** proposes a union between **{char_a.name}** and **{char_b.name}**.",
                    color=discord.Color.purple(),
                )
                view = ProposalView(
                    initiator=ctx.author,
                    consenter=consenter_member,
                    action_name=action_name,
                    proposal_embed=proposal_embed,
                    on_accept_callback=on_accept,
                )
                await ctx.send(
                    f"{consenter_member.mention}, a proposal awaits your decision.",
                    embed=proposal_embed,
                    view=view,
                )

    async def _send_gm_levy_alert(
        self,
        *,
        ctx: commands.Context,
        liege_house: House,  # Now directly take House object
        rally_point: str,
        results: List[str],
        player_vassals: List[dict],
        levy_type: Literal["Land", "Naval"],
    ):
        """
        A robust, reusable helper to send detailed alerts to the #gm-alerts channel.
        """
        channel = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")

        if not channel:
            print(
                f"GM Alert failed: Could not find channel named 'gm-alerts' in guild {ctx.guild.id}"
            )
            return

        success_count = sum(1 for r in results if r.startswith("✅"))
        fail_count = sum(1 for r in results if r.startswith("⚠️"))
        no_units_count = sum(1 for r in results if r.startswith(("🍂", "💨")))

        title = (
            "GM Alert: Banners Called"
            if levy_type == "Land"
            else "GM Alert: Naval Levies Called"
        )
        color = (
            discord.Color.gold() if levy_type == "Land" else discord.Color.dark_blue()
        )

        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.description = f"**{liege_house.name}** has summoned their {levy_type.lower()} vassals to **{rally_point}**."

        # If this was a GM-initiated call for an NPC, we indicate the GM initiated it
        if ctx.author.guild_permissions.administrator:  # Simplified check for admin/GM
            embed.add_field(
                name="Initiated By GM",
                value=f"{ctx.author.mention} (`{ctx.author.display_name}`)",
                inline=False,
            )
        else:  # Regular player call
            embed.add_field(
                name="Liege Lord",
                value=f"{ctx.author.mention} (`{ctx.author.display_name}`)",
                inline=False,
            )

        unit_type = "Levies" if levy_type == "Land" else "Fleets"
        summary_value = (
            f"Player Vassals Notified: **{len(player_vassals)}**\n"
            f"NPC {unit_type} Responding: **{success_count}**\n"
            f"NPCs Unable to Muster: **{fail_count}**\n"
            f"NPCs with No Units: **{no_units_count}**"
        )
        embed.add_field(name="Muster Summary", value=summary_value, inline=False)
        embed.add_field(
            name="Context",
            value=f"[Jump to Command]({ctx.message.jump_url})",
            inline=False,
        )
        embed.set_footer(text=f"Liege House ID: {liege_house.house_id}")

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            print(f"GM Alert failed: Bot lacks permissions in #{channel.name}")
        except Exception as e:
            print(f"An unexpected error occurred sending GM alert: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """This is crucial for making the views persistent."""
        print("Checking for pending banner calls...")
        async with get_session() as session:
            stmt = select(PendingBannerCall).where(
                PendingBannerCall.status == "PENDING_APPROVAL"
            )
            pending_calls = (await session.execute(stmt)).scalars().all()
            for call in pending_calls:
                self.bot.add_view(
                    BannerControlView(call.id), message_id=call.gm_message_id
                )
            print(f"Re-initialized {len(pending_calls)} pending banner control panels.")

    # @commands.command(name="call_banners")
    # @commands.cooldown(1, 360, commands.BucketType.user)  # 6 Minutes
    # @commands.check(is_in_house_channel)
    # async def call_banners(self, ctx, *, rally_point: str):
    #     """Initiates a banner call. Notifies players and creates GM ticket for NPCs."""
    #     player_wait_msg = await ctx.send("🦅 **Preparing banner call...**")

    #     async with get_session() as session:
    #         game = await GameRepo.get_active_game(session, ctx.guild.id)
    #         if not game:
    #             return await player_wait_msg.edit(content="❌ No active game.")

    #         # --- 1. GET PLAYER & HOUSE FIRST (Standard Player Logic) ---
    #         stmt_l = (
    #             select(GamePlayer)
    #             .join(User)
    #             .where(
    #                 User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
    #             )
    #             .options(selectinload(GamePlayer.house))
    #         )
    #         liege_p = (await session.execute(stmt_l)).scalars().first()

    #         if not liege_p or not liege_p.claimed_house_id:
    #             return await player_wait_msg.edit(
    #                 content="❌ You do not command a house."
    #             )

    #         # --- 2. SPAM CHECK ---
    #         stmt_check = select(PendingBannerCall).where(
    #             PendingBannerCall.game_id == game.game_id,
    #             PendingBannerCall.liege_house_id == liege_p.claimed_house_id,
    #             PendingBannerCall.status == "PENDING_APPROVAL",
    #         )
    #         existing_call = (await session.execute(stmt_check)).scalars().first()

    #         if existing_call:
    #             return await player_wait_msg.edit(
    #                 content=f"❌ **Hold:** You already have a banner call pending approval (ID: {existing_call.id}). Please wait for the GMs to resolve it."
    #             )

    #         # --- 3. SERVICE CALL (Standard Player Logic) ---
    #         service = DiplomacyService(session)
    #         success, npc_data, player_vassals = await service.prepare_banner_call(
    #             game.game_id,
    #             liege_discord_id=ctx.author.id,  # Pass discord_id as before
    #         )

    #         if not success:
    #             return await player_wait_msg.edit(
    #                 content="❌ Could not prepare call. Ensure you are the head of a house."
    #             )

    #         if not npc_data and not player_vassals:
    #             return await player_wait_msg.edit(
    #                 content="❌ You have no vassals to call."
    #             )

    #         # --- 4. NOTIFY PLAYER VASSALS ---
    #         sent_count = 0
    #         liege_name = f"House {liege_p.house.name}"

    #         if player_vassals:
    #             for pv in player_vassals:
    #                 chan_name = f"{pv['house_name'].lower().replace(' ', '-')}-quarters"
    #                 vassal_channel = discord.utils.get(
    #                     ctx.guild.text_channels, name=chan_name
    #                 )

    #                 embed = discord.Embed(
    #                     title="🦅 A Call to Arms!",
    #                     description=f"**{liege_name}** has called the banners!\n\n"
    #                     f"My Lord of **{pv['house_name']}**, your liege summons your forces to rally at **{rally_point}**.",
    #                     color=discord.Color.dark_red(),
    #                 )
    #                 embed.add_field(
    #                     name="Instructions",
    #                     value=f"Muster your troops and use `!march` to proceed to **{rally_point}**.",
    #                 )
    #                 embed.set_thumbnail(url="https://img.icons8.com/color/96/war.png")

    #                 try:
    #                     if vassal_channel:
    #                         await vassal_channel.send(
    #                             f"<@{pv['user_id']}>", embed=embed
    #                         )
    #                         sent_count += 1
    #                     else:
    #                         member = await ctx.guild.fetch_member(pv["user_id"])
    #                         if member:
    #                             await member.send(
    #                                 f"⚠️ **Banner Call:** Your house channel was not found.",
    #                                 embed=embed,
    #                             )
    #                             sent_count += 1
    #                 except Exception as e:
    #                     print(f"Failed to notify {pv['house_name']}: {e}")
    #                     continue

    #         # --- 5. HANDLE NPC VASSALS (GM Panel) ---
    #         gm_msg_part = ""

    #         if npc_data:
    #             gm_channel = discord.utils.get(
    #                 ctx.guild.text_channels, name="gm-alerts"
    #             )
    #             if not gm_channel:
    #                 return await player_wait_msg.edit(
    #                     content="❌ Error: #gm-alerts channel missing."
    #                 )

    #             vassal_data_for_db = [
    #                 {
    #                     "house_id": v["house"].house_id,
    #                     "house_name": v["house"].name,
    #                     "max_troops": v["troops"],
    #                     "percent": v["percent"],
    #                     "tag": v.get("tag", ""),
    #                     "home_x": v.get("home_x", 0),
    #                     "home_y": v.get("home_y", 0),
    #                 }
    #                 for v in npc_data
    #             ]

    #             new_pending_call = PendingBannerCall(
    #                 game_id=game.game_id,
    #                 guild_id=ctx.guild.id,
    #                 channel_id=ctx.channel.id,
    #                 message_id=player_wait_msg.id,
    #                 gm_channel_id=gm_channel.id,
    #                 gm_message_id=0,
    #                 liege_house_id=liege_p.house.house_id,
    #                 rally_point_name=rally_point,
    #                 vassal_data=vassal_data_for_db,
    #             )
    #             session.add(new_pending_call)
    #             await session.flush()

    #             view = BannerControlView(new_pending_call.id)
    #             embed = await view.create_embed(pending_call=new_pending_call)
    #             gm_panel_msg = await gm_channel.send(embed=embed, view=view)

    #             new_pending_call.gm_message_id = gm_panel_msg.id
    #             await session.commit()

    #             gm_msg_part = " NPC levies have been requested from the GMs."

    #         await player_wait_msg.edit(
    #             content=f"✅ **Call Sent!** Ravens dispatched to {sent_count} player vassals.{gm_msg_part} Rally Point: **{rally_point}**."
    #         )

    @commands.command(name="call_banners")
    @commands.cooldown(1, 360, commands.BucketType.user)  # 6 Minutes
    @commands.check(is_in_house_channel)
    async def call_banners(self, ctx, *, rally_point: str):
        """Initiates a banner call. Notifies players and creates GM ticket for NPCs."""
        player_wait_msg = await ctx.send(
            f"🦅 **Verifying rally point '{rally_point}'...**"
        )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await player_wait_msg.edit(content="❌ No active game.")

            # --- 0. VALIDATE LOCATION (NEW) ---
            # Prevents crashes later if the location is misspelled
            from app.services.warfare_service import WarfareService

            war_service = WarfareService(session)
            rally_coords = await war_service._get_location_from_db(
                game.game_id, rally_point
            )

            if not rally_coords:
                return await player_wait_msg.edit(
                    content=f"❌ **Invalid Location:** '{rally_point}' not found. Please check spelling or use coordinates (x,y)."
                )

            # --- 1. GET PLAYER & HOUSE ---
            stmt_l = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
                .options(selectinload(GamePlayer.house))
            )
            liege_p = (await session.execute(stmt_l)).scalars().first()

            if not liege_p or not liege_p.claimed_house_id:
                return await player_wait_msg.edit(
                    content="❌ You do not command a house."
                )

            # --- 2. SPAM CHECK ---
            stmt_check = select(PendingBannerCall).where(
                PendingBannerCall.game_id == game.game_id,
                PendingBannerCall.liege_house_id == liege_p.claimed_house_id,
                PendingBannerCall.status == "PENDING_APPROVAL",
            )
            existing_call = (await session.execute(stmt_check)).scalars().first()

            if existing_call:
                return await player_wait_msg.edit(
                    content=f"❌ **Hold:** You already have a banner call pending approval (ID: {existing_call.id}). Please wait for the GMs to resolve it."
                )

            await player_wait_msg.edit(content="🦅 **Preparing banner call...**")

            # --- 3. SERVICE CALL ---
            service = DiplomacyService(session)
            success, npc_data, player_vassals = await service.prepare_banner_call(
                game.game_id,
                liege_discord_id=ctx.author.id,
            )

            if not success:
                return await player_wait_msg.edit(
                    content="❌ Could not prepare call. Ensure you are the head of a house."
                )

            if not npc_data and not player_vassals:
                return await player_wait_msg.edit(
                    content="❌ You have no vassals to call."
                )

            # --- 4. NOTIFY PLAYER VASSALS ---
            sent_count = 0
            liege_name = f"House {liege_p.house.name}"

            if player_vassals:
                for pv in player_vassals:
                    chan_name = f"{pv['house_name'].lower().replace(' ', '-')}-quarters"
                    vassal_channel = discord.utils.get(
                        ctx.guild.text_channels, name=chan_name
                    )

                    embed = discord.Embed(
                        title="🦅 A Call to Arms!",
                        description=f"**{liege_name}** has called the banners!\n\n"
                        f"My Lord of **{pv['house_name']}**, your liege summons your forces to rally at **{rally_point}**.",
                        color=discord.Color.dark_red(),
                    )
                    embed.add_field(
                        name="Instructions",
                        value=f"Muster your troops and use `!march` to proceed to **{rally_point}**.",
                    )
                    embed.set_thumbnail(url="https://img.icons8.com/color/96/war.png")

                    try:
                        if vassal_channel:
                            await vassal_channel.send(
                                f"<@{pv['user_id']}>", embed=embed
                            )
                            sent_count += 1
                        else:
                            member = await ctx.guild.fetch_member(pv["user_id"])
                            if member:
                                await member.send(
                                    f"⚠️ **Banner Call:** Your house channel was not found.",
                                    embed=embed,
                                )
                                sent_count += 1
                    except Exception as e:
                        print(f"Failed to notify {pv['house_name']}: {e}")
                        continue

            # --- 5. HANDLE NPC VASSALS (GM Panel) ---
            gm_msg_part = ""

            if npc_data:
                gm_channel = discord.utils.get(
                    ctx.guild.text_channels, name="gm-alerts"
                )
                if not gm_channel:
                    return await player_wait_msg.edit(
                        content="❌ Error: #gm-alerts channel missing."
                    )

                # FIX: Use safe dictionary access matching the updated service
                vassal_data_for_db = [
                    {
                        "house_id": v["house_id"],
                        "house_name": v["house_name"],  # This name already has the "*"
                        "max_troops": v[
                            "max_amount"
                        ],  # Note: Service returns 'max_amount' now, check your service return keys!
                        "percent": v["percent"],
                        "home_x": v.get("home_x", 0),
                        "home_y": v.get("home_y", 0),
                        "breakdown": v.get("breakdown", ""),  # <--- ADD THIS LINE
                    }
                    for v in npc_data
                ]
                new_pending_call = PendingBannerCall(
                    game_id=game.game_id,
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    message_id=player_wait_msg.id,
                    gm_channel_id=gm_channel.id,
                    gm_message_id=0,
                    liege_house_id=liege_p.house.house_id,
                    rally_point_name=rally_point,
                    vassal_data=vassal_data_for_db,
                    call_type="LAND",
                )
                session.add(new_pending_call)
                await session.flush()

                view = BannerControlView(new_pending_call.id)
                # Player initiated -> gm_initiator is None
                embed = await view.create_embed(pending_call=new_pending_call)
                gm_panel_msg = await gm_channel.send(embed=embed, view=view)

                new_pending_call.gm_message_id = gm_panel_msg.id
                await session.commit()

                gm_msg_part = " NPC levies have been requested from the GMs."

            await player_wait_msg.edit(
                content=f"✅ **Call Sent!** Ravens dispatched to {sent_count} player vassals.{gm_msg_part} Rally Point: **{rally_point}**."
            )

    # @commands.Cog.listener()
    # async def on_interaction(self, interaction: discord.Interaction):
    #     """
    #     Listener for handling the persistent banner control buttons for both land and sea.
    #     """
    #     custom_id = interaction.data.get("custom_id")
    #     if not custom_id or not custom_id.startswith("banner_"):
    #         return

    #     parts = custom_id.split("_")
    #     if len(parts) < 3:
    #         return

    #     action, pending_call_id_str = parts[1], parts[-1]
    #     try:
    #         pending_call_id = int(pending_call_id_str)
    #     except ValueError:
    #         return

    #     async with get_session() as session:
    #         service = DiplomacyService(session)
    #         pending_call = await session.get(PendingBannerCall, pending_call_id)

    #         if not pending_call:
    #             await interaction.response.send_message(
    #                 "❌ This banner call has expired or could not be found.",
    #                 ephemeral=True,
    #             )
    #             return

    #         if pending_call.status != "PENDING_APPROVAL" and action in [
    #             "confirm",
    #             "cancel",
    #         ]:
    #             await interaction.response.send_message(
    #                 "❌ Action has already been taken on this banner call.",
    #                 ephemeral=True,
    #             )
    #             return

    #         if action == "confirm":
    #             await interaction.response.defer()

    #             player_channel = self.bot.get_channel(pending_call.channel_id)
    #             player_msg = None
    #             if player_channel:
    #                 try:
    #                     player_msg = await player_channel.fetch_message(
    #                         pending_call.message_id
    #                     )
    #                 except discord.NotFound:
    #                     print(
    #                         f"Could not find original player message {pending_call.message_id} to update."
    #                     )

    #             if pending_call.call_type == "LAND":
    #                 march_results = await service.execute_muster_from_pending_call(
    #                     pending_call_id
    #                 )
    #                 if player_msg:
    #                     embeds = []
    #                     if march_results:
    #                         for i in range(0, len(march_results), 10):
    #                             chunk = march_results[i : i + 10]
    #                             embed = discord.Embed(
    #                                 title="Banner Call Report: Levies Mustered!",
    #                                 description=f"Your vassals' levies are now marching on **{pending_call.rally_point_name}**.",
    #                                 color=discord.Color.green(),
    #                             )
    #                             embed.add_field(
    #                                 name="Levy Muster Status",
    #                                 value="\n".join(chunk),
    #                                 inline=False,
    #                             )
    #                             embeds.append(embed)
    #                     else:
    #                         embeds.append(
    #                             discord.Embed(
    #                                 title="Banner Call Report",
    #                                 description="No levies were mustered.",
    #                                 color=discord.Color.orange(),
    #                             )
    #                         )
    #                     paginator = Paginator(embeds)
    #                     await player_msg.edit(
    #                         content="**GM Approved!** Your banner call has been executed.",
    #                         embed=paginator.embeds[0],
    #                         view=paginator,
    #                     )

    #             elif pending_call.call_type == "SEA":
    #                 sail_results = await service.execute_sea_muster_from_pending_call(
    #                     pending_call_id
    #                 )
    #                 if player_msg:
    #                     embeds = []
    #                     if sail_results:
    #                         for i in range(0, len(sail_results), 10):
    #                             chunk = sail_results[i : i + 10]
    #                             embed = discord.Embed(
    #                                 title="🌊 Fleet Muster Report: Fleets Mustered!",
    #                                 description=f"Your vassal fleets are setting sail for **{pending_call.rally_point_name}**.",
    #                                 color=discord.Color.green(),
    #                             )
    #                             embed.add_field(
    #                                 name="Fleet Status",
    #                                 value="\n".join(chunk),
    #                                 inline=False,
    #                             )
    #                             embeds.append(embed)
    #                     else:
    #                         embeds.append(
    #                             discord.Embed(
    #                                 title="Fleet Muster Report",
    #                                 description="No fleets were mustered.",
    #                                 color=discord.Color.orange(),
    #                             )
    #                         )
    #                     paginator = Paginator(embeds)
    #                     await player_msg.edit(
    #                         content="**GM Approved!** Your naval levy call has been executed.",
    #                         embed=paginator.embeds[0],
    #                         view=paginator,
    #                     )

    #             view = BannerControlView(pending_call.id)
    #             await interaction.edit_original_response(
    #                 content="✅ **Muster Complete!** Report sent to the player.",
    #                 embed=await view.create_embed(),
    #                 view=None,
    #             )

    #         elif action == "cancel":
    #             pending_call.status = "CANCELLED"
    #             await session.commit()

    #             player_channel = self.bot.get_channel(pending_call.channel_id)
    #             if player_channel:
    #                 try:
    #                     player_msg = await player_channel.fetch_message(
    #                         pending_call.message_id
    #                     )
    #                     await player_msg.edit(
    #                         content=f"❌ **A GM has cancelled your banner call for {pending_call.rally_point_name}.**",
    #                         embed=None,
    #                         view=None,
    #                     )
    #                 except discord.NotFound:
    #                     pass

    #             view = BannerControlView(pending_call.id)
    #             await interaction.response.edit_message(
    #                 embed=await view.create_embed(), view=None
    #             )

    #         elif action == "help":
    #             unit_name = "ships" if pending_call.call_type == "SEA" else "troops"
    #             help_text = (
    #                 "**How to use this panel:**\n"
    #                 f"1. **Review:** See the default levy contributions ({unit_name}).\n"
    #                 f"2. **Adjust:** Use the dropdown menu to select a vassal. A pop-up will ask for the new percentage (0-100) of {unit_name} you want them to contribute.\n"
    #                 "3. **Confirm:** Once all percentages are correct, click `Confirm & Muster Levies`. This is the final step and cannot be undone.\n"
    #                 "4. **Cancel:** If the banner call should not proceed, click `Cancel Banner Call`."
    #             )
    #             await interaction.response.send_message(help_text, ephemeral=True)

    #         elif action == "adjust":
    #             from app.ui.banner_view import AdjustVassalModal

    #             modal = AdjustVassalModal(pending_call.id, pending_call.call_type)
    #             await interaction.response.send_modal(modal)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """
        Handles interactions for Banner Call buttons (Confirm/Cancel).
        """
        custom_id = interaction.data.get("custom_id")
        if not custom_id or not custom_id.startswith("banner_"):
            return

        # Acknowledge the interaction immediately so the button stops spinning
        # We use defer() so we have 15 minutes to reply/edit
        await interaction.response.defer()

        # Parse ID: "banner_confirm_123" -> action="confirm", call_id=123
        try:
            parts = custom_id.split("_")
            action = parts[1]
            call_id = int(parts[2])
        except (IndexError, ValueError):
            return await interaction.followup.send(
                "❌ Invalid button data.", ephemeral=True
            )

        async with get_session() as session:
            service = DiplomacyService(session)

            # --- HANDLE CANCEL ---
            if action == "cancel":
                stmt = select(PendingBannerCall).where(PendingBannerCall.id == call_id)
                call = (await session.execute(stmt)).scalars().first()

                if call:
                    call.status = "CANCELLED"
                    await session.commit()
                    embed = discord.Embed(
                        title="❌ Banner Call Cancelled", color=discord.Color.red()
                    )
                    await interaction.edit_original_response(embed=embed, view=None)
                else:
                    await interaction.followup.send(
                        "❌ Call not found.", ephemeral=True
                    )
                return

            # --- HANDLE CONFIRM ---
            if action == "confirm":
                # 1. Send Feedback Message
                loading_msg = await interaction.followup.send(
                    "⏳ **Mobilizing the realm...** Calculating routes and spawning armies. This may take a moment.",
                    ephemeral=True,
                )

                try:
                    # 2. Execute Logic
                    march_results = await service.execute_muster_from_pending_call(
                        call_id
                    )

                    if not march_results:
                        # If list is empty, it means the call wasn't found or wasn't pending
                        await loading_msg.edit(
                            content="❌ **Error:** Could not execute muster. The call may have expired or already been processed."
                        )
                        return

                    # 3. Success - Update the original Panel
                    embed = discord.Embed(
                        title="✅ Muster Complete!",
                        description="**Report sent to the player.**\n\n"
                        + "\n".join(march_results[:20]),  # Show first 20 lines
                        color=discord.Color.green(),
                    )
                    if len(march_results) > 20:
                        embed.set_footer(
                            text=f"...and {len(march_results)-20} more updates."
                        )

                    # Edit the GM Panel message to show it's done (remove buttons)
                    await interaction.edit_original_response(embed=embed, view=None)

                    # 4. Notify the Liege Player (if applicable)
                    # We need to fetch the call again (or use the returned data if you modified the service return)
                    # For simplicity, we just confirm to the GM here.
                    await loading_msg.edit(
                        content="✅ **Done!** Armies have been created and orders issued."
                    )

                except Exception as e:
                    import traceback

                    traceback.print_exc()
                    await loading_msg.edit(
                        content=f"❌ **System Error:** An error occurred during muster:\n`{str(e)}`"
                    )

    # @commands.command(name="call_levies_sea")
    # @commands.cooldown(1, 360, commands.BucketType.user)  # 1 use every 6 minutes
    # @commands.check(is_in_house_channel)
    # async def call_levies_sea(self, ctx, *, rally_point: str):
    #     """Initiates a naval levy call, pending GM approval."""
    #     player_wait_msg = await ctx.send(
    #         "🌊 **Preparing naval levy call... This will require GM approval.**"
    #     )

    #     async with get_session() as session:
    #         game = await GameRepo.get_active_game(session, ctx.guild.id)
    #         if not game:
    #             return await player_wait_msg.edit(
    #                 content="❌ No active game found for this server."
    #             )

    #         stmt_p = (
    #             select(GamePlayer)
    #             .join(User, GamePlayer.user_id == User.user_id)
    #             .where(
    #                 User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
    #             )
    #             .options(selectinload(GamePlayer.house))
    #         )
    #         liege_player = (await session.execute(stmt_p)).scalars().first()

    #         if not liege_player or not liege_player.house:
    #             return await player_wait_msg.edit(
    #                 content="❌ Could not find a house for you in the active game."
    #             )

    #         stmt_check = select(PendingBannerCall).where(
    #             PendingBannerCall.game_id == game.game_id,
    #             PendingBannerCall.liege_house_id == liege_player.house.house_id,
    #             PendingBannerCall.status == "PENDING_APPROVAL",
    #             PendingBannerCall.call_type == "SEA",
    #         )
    #         existing_call = (await session.execute(stmt_check)).scalars().first()

    #         if existing_call:
    #             return await player_wait_msg.edit(
    #                 content=f"❌ **Hold:** You already have a naval levy call pending approval (ID: {existing_call.id}). Please wait for the GMs to resolve it."
    #             )

    #         service = DiplomacyService(session)
    #         success, vassal_data, player_vassals = await service.prepare_sea_levy_call(
    #             game.game_id,
    #             liege_discord_id=ctx.author.id,  # Pass discord_id as before
    #         )

    #         if not success:
    #             return await player_wait_msg.edit(
    #                 content="❌ Could not prepare the naval levy call. Ensure you are the head of a house."
    #             )

    #         if not vassal_data and not player_vassals:
    #             return await player_wait_msg.edit(
    #                 content="❌ You have no naval vassals (NPC or Player) to call."
    #             )

    #         if player_vassals:
    #             notified_count = 0
    #             for pv in player_vassals:
    #                 chan_name = f"{pv['house_name'].lower().replace(' ', '-')}-quarters"
    #                 channel = discord.utils.get(ctx.guild.text_channels, name=chan_name)

    #                 embed = discord.Embed(
    #                     title="🌊 A Call for Fleets!", color=discord.Color.blue()
    #                 )
    #                 embed.description = f"**{ctx.author.display_name}** calls the fleets to rally at **{rally_point}**!"
    #                 embed.add_field(
    #                     name="Orders",
    #                     value="Report the status of your fleet and set sail immediately.",
    #                     inline=False,
    #                 )
    #                 embed.set_footer(
    #                     text="To respond, use !sail or contact your Liege."
    #                 )

    #                 try:
    #                     if channel:
    #                         await channel.send(f"<@{pv['discord_id']}>", embed=embed)
    #                         notified_count += 1
    #                     else:
    #                         member = await ctx.guild.fetch_member(pv["discord_id"])
    #                         if member:
    #                             await member.send(
    #                                 f"⚠️ **Naval Call:** House channel missing.",
    #                                 embed=embed,
    #                             )
    #                             notified_count += 1
    #                 except Exception as e:
    #                     print(f"Failed to notify naval vassal {pv['house_name']}: {e}")

    #             await ctx.send(
    #                 f"📨 **Messages Sent:** Notified {notified_count} player vassals with ports."
    #             )

    #         if not vassal_data:
    #             return await player_wait_msg.edit(
    #                 content=f"✅ **Call Sent!** Notified players. No NPC fleets were found to muster."
    #             )

    #         gm_channel = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")
    #         if not gm_channel:
    #             return await player_wait_msg.edit(
    #                 content="❌ Critical error: #gm-alerts channel not found. Cannot proceed with NPC muster."
    #             )

    #         vassal_data_for_db = [
    #             {
    #                 "house_id": v["house"].house_id,
    #                 "house_name": v["house"].name,
    #                 "max_ships": v["ships"],
    #                 "percent": v["percent"],
    #                 "start_location": v["start_location"],
    #             }
    #             for v in vassal_data
    #         ]

    #         new_pending_call = PendingBannerCall(
    #             game_id=game.game_id,
    #             guild_id=ctx.guild.id,
    #             channel_id=ctx.channel.id,
    #             message_id=player_wait_msg.id,
    #             gm_channel_id=gm_channel.id,
    #             gm_message_id=0,
    #             liege_house_id=liege_player.house.house_id,
    #             rally_point_name=rally_point,
    #             vassal_data=vassal_data_for_db,
    #             call_type="SEA",
    #         )
    #         session.add(new_pending_call)
    #         await session.flush()

    #         view = BannerControlView(new_pending_call.id)
    #         embed = await view.create_embed(pending_call=new_pending_call)
    #         gm_panel_msg = await gm_channel.send(embed=embed, view=view)

    #         new_pending_call.gm_message_id = gm_panel_msg.id
    #         await session.commit()

    #         await player_wait_msg.edit(
    #             content=f"✅ **Your naval levy call for {rally_point} has been sent to the GMs for approval and adjustment.** You will be notified when they muster the fleets."
    #         )
    @commands.command(name="call_levies_sea")
    @commands.cooldown(1, 360, commands.BucketType.user)  # 1 use every 6 minutes
    @commands.check(is_in_house_channel)
    async def call_levies_sea(self, ctx, *, rally_point: str):
        """Initiates a naval levy call, pending GM approval."""
        player_wait_msg = await ctx.send(
            f"🌊 **Verifying naval rally point '{rally_point}'...**"
        )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await player_wait_msg.edit(content="❌ No active game found.")

            # --- 0. VALIDATE LOCATION ---
            from app.services.warfare_service import WarfareService

            war_service = WarfareService(session)
            rally_coords = await war_service._get_location_from_db(
                game.game_id, rally_point
            )

            if not rally_coords:
                return await player_wait_msg.edit(
                    content=f"❌ **Invalid Location:** '{rally_point}' not found. Please check spelling or use coordinates."
                )

            # --- 1. GET PLAYER & HOUSE ---
            stmt_p = (
                select(GamePlayer)
                .join(User, GamePlayer.user_id == User.user_id)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
                .options(selectinload(GamePlayer.house))
            )
            liege_player = (await session.execute(stmt_p)).scalars().first()

            if not liege_player or not liege_player.house:
                return await player_wait_msg.edit(content="❌ You do not have a house.")

            # --- 2. SPAM CHECK ---
            stmt_check = select(PendingBannerCall).where(
                PendingBannerCall.game_id == game.game_id,
                PendingBannerCall.liege_house_id == liege_player.house.house_id,
                PendingBannerCall.status == "PENDING_APPROVAL",
                PendingBannerCall.call_type == "SEA",
            )
            existing_call = (await session.execute(stmt_check)).scalars().first()

            if existing_call:
                return await player_wait_msg.edit(
                    content=f"❌ **Hold:** You already have a naval call pending (ID: {existing_call.id})."
                )

            await player_wait_msg.edit(content="🌊 **Preparing naval levy call...**")

            # --- 3. SERVICE CALL ---
            service = DiplomacyService(session)
            success, vassal_data, player_vassals = await service.prepare_sea_levy_call(
                game.game_id,
                liege_discord_id=ctx.author.id,
            )

            if not success:
                return await player_wait_msg.edit(content="❌ Could not prepare call.")

            if not vassal_data and not player_vassals:
                return await player_wait_msg.edit(
                    content="❌ You have no naval vassals."
                )

            # --- 4. NOTIFY PLAYERS ---
            if player_vassals:
                notified_count = 0
                for pv in player_vassals:
                    chan_name = f"{pv['house_name'].lower().replace(' ', '-')}-quarters"
                    channel = discord.utils.get(ctx.guild.text_channels, name=chan_name)

                    embed = discord.Embed(
                        title="🌊 A Call for Fleets!", color=discord.Color.blue()
                    )
                    embed.description = f"**{ctx.author.display_name}** calls the fleets to **{rally_point}**!"
                    embed.add_field(
                        name="Orders", value="Set sail immediately.", inline=False
                    )

                    try:
                        if channel:
                            await channel.send(f"<@{pv['discord_id']}>", embed=embed)
                            notified_count += 1
                        else:
                            member = await ctx.guild.fetch_member(pv["discord_id"])
                            if member:
                                await member.send(
                                    "⚠️ **Naval Call:** House channel missing.",
                                    embed=embed,
                                )
                                notified_count += 1
                    except Exception:
                        pass  # Ignore failures

                await ctx.send(
                    f"📨 **Messages Sent:** Notified {notified_count} player vassals."
                )

            # --- 5. HANDLE NPC VASSALS ---
            if not vassal_data:
                return await player_wait_msg.edit(
                    content=f"✅ **Call Sent!** Notified players (No NPC fleets found)."
                )

            gm_channel = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")
            if not gm_channel:
                return await player_wait_msg.edit(
                    content="❌ Critical error: #gm-alerts missing."
                )

            # SAFE MAPPING (Use safe dict keys)
            vassal_data_for_db = [
                {
                    "house_id": v["house_id"],
                    "house_name": v["house_name"],
                    "max_ships": v.get("ships", 0),  # Ensure keys match service return
                    "percent": v.get("percent", 0.0),
                    "home_x": v.get("home_x", 0),  # Critical
                    "home_y": v.get("home_y", 0),  # Critical
                }
                for v in vassal_data
            ]

            new_pending_call = PendingBannerCall(
                game_id=game.game_id,
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                message_id=player_wait_msg.id,
                gm_channel_id=gm_channel.id,
                gm_message_id=0,
                liege_house_id=liege_player.house.house_id,
                rally_point_name=rally_point,
                vassal_data=vassal_data_for_db,
                call_type="SEA",
            )
            session.add(new_pending_call)
            await session.flush()

            view = BannerControlView(new_pending_call.id)
            # Player initiated -> No gm_initiator
            embed = await view.create_embed(pending_call=new_pending_call)
            gm_panel_msg = await gm_channel.send(embed=embed, view=view)

            new_pending_call.gm_message_id = gm_panel_msg.id
            await session.commit()

            await player_wait_msg.edit(
                content=f"✅ **Naval Call Sent!** Awaiting GM approval for NPC fleets."
            )

    @commands.command(name="vassals")
    @commands.check(is_in_house_channel)
    async def list_vassals(self, ctx):
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
                .options(selectinload(GamePlayer.house))
            )
            player = (await session.execute(stmt)).scalars().first()

            if not player or not player.claimed_house_id:
                return await ctx.send("❌ No house.")

            stmt_v = (
                select(House)
                .where(
                    House.liege_id == player.claimed_house_id,
                    House.game_id == game.game_id,
                )
                .options(selectinload(House.armies), selectinload(House.fiefs))
            )
            vassals = (await session.execute(stmt_v)).scalars().all()

            if not vassals:
                return await ctx.send("🍂 No vassals.")

            lines = []
            for v in vassals:
                troops = sum(a.troop_count for a in v.armies)
                lines.append(f"**{v.name}** | 🏰 {len(v.fiefs)}")

            chunks = [lines[i : i + 10] for i in range(0, len(lines), 10)]
            embeds = [
                discord.Embed(
                    title=f"Banners of {player.house.name}",
                    description="\n".join(c),
                    color=discord.Color.gold(),
                )
                for c in chunks
            ]

            if embeds:
                await ctx.send(
                    embed=embeds[0], view=Paginator(embeds) if len(embeds) > 1 else None
                )

    @commands.command(name="declare_fealty")
    @commands.check(is_in_house_channel)
    async def declare_fealty(self, ctx, *, new_liege: str):
        """
        Swear loyalty to a new House.
        Usage: !declare_fealty Targaryen
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return await ctx.send("❌ You are not in the game.")

            service = DiplomacyService(session)
            success, msg = await service.declare_fealty(
                game.game_id, vassal_user_id=user.user_id, new_liege_name=new_liege
            )

            if success:
                dec_channel = discord.utils.get(
                    ctx.guild.text_channels, name="declarations"
                )
                if dec_channel:
                    embed = discord.Embed(
                        title="📜 Declaration of Fealty",
                        description=msg,
                        color=discord.Color.blue(),
                    )
                    await dec_channel.send(embed=embed)

            await ctx.send(msg)

    @commands.command(name="declare_war")
    @commands.check(is_in_house_channel)
    async def declare_war(self, ctx, target: str, *, reason: str = "Aggression"):
        """
        Declare war on a House or Character.
        (This command currently only sends an announcement, no game state changes in service)
        """
        dec_channel = discord.utils.get(ctx.guild.text_channels, name="declarations")
        if dec_channel:
            embed = discord.Embed(
                title="⚔️ Declaration of War", color=discord.Color.red()
            )
            embed.description = (
                f"**{ctx.author.display_name}** has declared WAR on **{target}**!"
            )
            embed.add_field(name="Casus Belli", value=reason)
            await dec_channel.send(embed=embed)
            await ctx.send("✅ War declared.")
        else:
            await ctx.send("❌ Declarations channel not found.")

    @commands.command(name="disband_levies")
    @commands.check(is_in_house_channel)
    async def disband_levies(self, ctx):
        """
        Returns all NPC levies to their home castles.
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return

            service = DiplomacyService(session)
            success, result = await service.disband_levies(game.game_id, user.user_id)

            if success:
                if not result:
                    await ctx.send("ℹ️ No active levies were found to disband.")
                    return

                embeds = []
                chunk_size = 15
                for i in range(0, len(result), chunk_size):
                    chunk = result[i : i + chunk_size]

                    embed = discord.Embed(
                        title="🏳️ Levies Disbanded",
                        description="\n".join(chunk),
                        color=discord.Color.blue(),
                    )
                    embeds.append(embed)

                paginator_view = Paginator(embeds)
                await ctx.send(embed=paginator_view.embeds[0], view=paginator_view)
            else:
                await ctx.send(result)

    @commands.command(name="meet")
    async def meet(
        self,
        ctx: commands.Context,
        target: discord.Member,
        *,
        location: str = "A Private Setting",
    ):
        """
        Requests a private meeting with another player, creating a channel upon consent.
        Usage: !meet @PlayerName location="The Wolf's Den"
        """
        if target.bot or target == ctx.author:
            return await ctx.send("❌ You cannot meet with a bot or yourself.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game is running on this server.")

            initiator_player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
                .options(
                    selectinload(GamePlayer.character), selectinload(GamePlayer.house)
                )
            )
            target_player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(User.discord_id == target.id, GamePlayer.game_id == game.game_id)
                .options(
                    selectinload(GamePlayer.character), selectinload(GamePlayer.house)
                )
            )

            if not initiator_player or not target_player:
                return await ctx.send(
                    "❌ One or both participants are not active players in the game."
                )

            initiator_name = (
                initiator_player.character.name
                if initiator_player.character
                else (
                    initiator_player.house.name
                    if initiator_player.house
                    else ctx.author.display_name
                )
            )
            target_name = (
                target_player.character.name
                if target_player.character
                else (
                    target_player.house.name
                    if target_player.house
                    else target.display_name
                )
            )

            async def on_accept(interaction: discord.Interaction):
                channel = await self._create_meeting_channel(
                    guild=interaction.guild,
                    member1=ctx.author,
                    member2=target,
                    location_str=location,
                    name1=initiator_name,
                    name2=target_name,
                )

                if channel:
                    await interaction.followup.send(
                        f"✅ The private meeting room has been created: {channel.mention}",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "❌ Failed to create the meeting room. Please check the bot's permissions.",
                        ephemeral=True,
                    )

            proposal_embed = discord.Embed(
                title="📜 A Request for Audience",
                description=f"**{initiator_name}** formally requests a private meeting with **{target_name}**.",
                color=discord.Color.blurple(),
            )

            view = ProposalView(
                initiator=ctx.author,
                consenter=target,
                action_name="Meeting",
                proposal_embed=proposal_embed,
                on_accept_callback=on_accept,
            )

            await ctx.send(
                f"{target.mention}, you have received a proposal.",
                embed=proposal_embed,
                view=view,
            )

    async def _create_meeting_channel(
        self,
        guild: discord.Guild,
        member1: discord.Member,
        member2: discord.Member,
        location_str: str,
        name1: str,
        name2: str,
    ) -> discord.TextChannel | None:
        """A generic helper to create a private meeting channel for two members."""

        category = discord.utils.get(guild.categories, name="Meetings")
        if not category:
            try:
                category = await guild.create_category("Meetings")
            except discord.Forbidden:
                print("ERROR: Bot lacks permission to create categories.")
                return None

        sanitized_name1 = re.sub(r"[^a-zA-Z0-9-]", "", name1.lower().replace(" ", "-"))
        sanitized_name2 = re.sub(r"[^a-zA-Z0-9-]", "", name2.lower().replace(" ", "-"))
        channel_name = f"meet-{sanitized_name1[:15]}-{sanitized_name2[:15]}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member1: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
            member2: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
        }

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"A private meeting between {name1} and {name2}.",
            )
        except discord.Forbidden:
            print(f"ERROR: Bot lacks permission to create channel '{channel_name}'.")
            return None

        welcome_embed = discord.Embed(
            title="Meeting Room",
            description=f"This is a private channel for **{name1}** and **{name2}**.",
            color=discord.Color.dark_grey(),
        )
        welcome_embed.add_field(name="📍 Location", value=location_str)
        await channel.send(
            f"The meeting may now begin. {member1.mention} {member2.mention}",
            embed=welcome_embed,
        )

        return channel

    @commands.command(name="marry")
    async def marry(self, ctx, *, query: str):
        """
        Arrange a marriage between two characters. Requires consent.
        Usage: !marry "[Character A]" to "[Character B]"
        """
        await self._handle_union_proposal(ctx, query, action_name="Marriage", icon="💍")

    @commands.command(name="betroth")
    async def betroth(self, ctx, *, query: str):
        """
        Arrange a betrothal between two characters. Requires consent.
        Usage: !betroth "[Character A]" to "[Character B]"
        """
        await self._handle_union_proposal(
            ctx, query, action_name="Betrothal", icon="📜"
        )

    # --- GM DIPLOMACY COMMANDS ---
    @commands.group(name="gm_diplomacy", invoke_without_command=True)
    @commands.check(is_gm)
    async def gm_diplomacy(self, ctx):
        """GM commands for diplomatic actions for NPCs."""
        await ctx.send(
            "GM Diplomacy Subcommands: `call_banners`, `call_levies_sea`, `declare_fealty`, `declare_war`."
        )

    @gm_diplomacy.command(name="call_banners")
    @commands.check(is_gm)
    async def gm_call_banners(self, ctx, target_house_id: int, *, rally_point: str):
        """GM: Make an NPC house call banners. Usage: !gm_diplomacy call_banners [HouseID] [RallyPoint]"""
        player_wait_msg = await ctx.send(
            f"🦅 **GM Initiated: Preparing banner call for House ID {target_house_id}...**"
        )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await player_wait_msg.edit(content="❌ No active game.")

            # Find the target NPC house
            liege_house_obj = await session.get(House, target_house_id)
            if not liege_house_obj:
                return await player_wait_msg.edit(
                    content=f"❌ NPC House with ID {target_house_id} not found."
                )
            if liege_house_obj.game_id != game.game_id:
                return await player_wait_msg.edit(
                    content=f"❌ House ID {target_house_id} does not belong to this game."
                )

            # SPAM CHECK (Same as player, but for the NPC house)
            stmt_check = select(PendingBannerCall).where(
                PendingBannerCall.game_id == game.game_id,
                PendingBannerCall.liege_house_id == target_house_id,
                PendingBannerCall.status == "PENDING_APPROVAL",
                PendingBannerCall.call_type == "LAND",
            )
            existing_call = (await session.execute(stmt_check)).scalars().first()

            if existing_call:
                return await player_wait_msg.edit(
                    content=f"❌ **Hold:** House {liege_house_obj.name} already has a banner call pending approval (ID: {existing_call.id})."
                )

            # SERVICE CALL (using GM override)
            service = DiplomacyService(session)
            success, npc_data, player_vassals = await service.prepare_banner_call(
                game.game_id,
                liege_discord_id=None,  # No direct discord ID for NPC
                acting_house_id=target_house_id,
                is_gm_override=True,
            )

            if not success:
                return await player_wait_msg.edit(
                    content=f"❌ Could not prepare call for House {liege_house_obj.name}. Ensure it is a valid liege."
                )

            if not npc_data and not player_vassals:
                return await player_wait_msg.edit(
                    content=f"❌ House {liege_house_obj.name} has no vassals to call."
                )

            # NOTIFY PLAYER VASSALS (Identical to player version)
            sent_count = 0
            liege_name = f"House {liege_house_obj.name}"

            if player_vassals:
                for pv in player_vassals:
                    chan_name = f"{pv['house_name'].lower().replace(' ', '-')}-quarters"
                    vassal_channel = discord.utils.get(
                        ctx.guild.text_channels, name=chan_name
                    )

                    embed = discord.Embed(
                        title="🦅 A Call to Arms! (GM Initiated)",
                        description=f"**{liege_name}** has called the banners!\n\n"
                        f"My Lord of **{pv['house_name']}**, your liege summons your forces to rally at **{rally_point}**.",
                        color=discord.Color.dark_red(),
                    )
                    embed.add_field(
                        name="Instructions",
                        value=f"Muster your troops and use `!march` to proceed to **{rally_point}**.",
                    )
                    embed.set_thumbnail(url="https://img.icons8.com/color/96/war.png")

                    try:
                        if vassal_channel:
                            await vassal_channel.send(
                                f"<@{pv['user_id']}>", embed=embed
                            )
                            sent_count += 1
                        else:
                            member = await ctx.guild.fetch_member(pv["user_id"])
                            if member:
                                await member.send(
                                    f"⚠️ **Banner Call (GM):** Your house channel was not found.",
                                    embed=embed,
                                )
                                sent_count += 1
                    except Exception as e:
                        print(f"Failed to notify {pv['house_name']}: {e}")
                        continue

            # HANDLE NPC VASSALS (GM Panel)
            gm_msg_part = ""

            if npc_data:
                gm_channel = discord.utils.get(
                    ctx.guild.text_channels, name="gm-alerts"
                )
                if not gm_channel:
                    return await player_wait_msg.edit(
                        content="❌ Error: #gm-alerts channel missing."
                    )

                vassal_data_for_db = [
                    {
                        "house_id": v["house_id"],
                        "house_name": v["house_name"],  # This name already has the "*"
                        "max_troops": v[
                            "max_amount"
                        ],  # Note: Service returns 'max_amount' now, check your service return keys!
                        "percent": v["percent"],
                        "home_x": v.get("home_x", 0),
                        "home_y": v.get("home_y", 0),
                        "breakdown": v.get("breakdown", ""),  # <--- ADD THIS LINE
                    }
                    for v in npc_data
                ]

                new_pending_call = PendingBannerCall(
                    game_id=game.game_id,
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,  # The GM's channel for now, can be adjusted
                    message_id=player_wait_msg.id,  # The GM's original message
                    gm_channel_id=gm_channel.id,
                    gm_message_id=0,
                    liege_house_id=target_house_id,  # The NPC house's ID
                    rally_point_name=rally_point,
                    vassal_data=vassal_data_for_db,
                    call_type="LAND",
                )
                session.add(new_pending_call)
                await session.flush()

                view = BannerControlView(new_pending_call.id)
                embed = await view.create_embed(
                    pending_call=new_pending_call, gm_initiator=ctx.author
                )  # Pass GM for display
                gm_panel_msg = await gm_channel.send(embed=embed, view=view)

                new_pending_call.gm_message_id = gm_panel_msg.id
                await session.commit()

                gm_msg_part = " NPC levies have been requested from the GMs."

            await player_wait_msg.edit(
                content=f"✅ **GM Initiated Call Sent!** Ravens dispatched to {sent_count} player vassals on behalf of House {liege_house_obj.name}.{gm_msg_part} Rally Point: **{rally_point}**."
            )

    @gm_diplomacy.command(name="call_levies_sea")
    @commands.check(is_gm)
    async def gm_call_levies_sea(self, ctx, target_house_id: int, *, rally_point: str):
        """GM: Make an NPC house call naval levies. Usage: !gm_diplomacy call_levies_sea [HouseID] [RallyPoint]"""
        player_wait_msg = await ctx.send(
            f"🌊 **Verifying naval rally point '{rally_point}'...**"
        )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await player_wait_msg.edit(content="❌ No active game.")

            # --- 0. VALIDATE LOCATION ---
            from app.services.warfare_service import WarfareService

            war_service = WarfareService(session)
            rally_coords = await war_service._get_location_from_db(
                game.game_id, rally_point
            )

            if not rally_coords:
                return await player_wait_msg.edit(
                    content=f"❌ **Invalid Location:** '{rally_point}' not found."
                )

            # Check Liege
            liege_house_obj = await session.get(House, target_house_id)
            if not liege_house_obj:
                return await player_wait_msg.edit(
                    content=f"❌ House ID {target_house_id} not found."
                )

            # Spam Check
            stmt_check = select(PendingBannerCall).where(
                PendingBannerCall.game_id == game.game_id,
                PendingBannerCall.liege_house_id == target_house_id,
                PendingBannerCall.status == "PENDING_APPROVAL",
                PendingBannerCall.call_type == "SEA",
            )
            existing_call = (await session.execute(stmt_check)).scalars().first()

            if existing_call:
                return await player_wait_msg.edit(
                    content=f"❌ **Hold:** Naval call already pending (ID: {existing_call.id})."
                )

            await player_wait_msg.edit(
                content=f"🌊 **Preparing GM naval call for {liege_house_obj.name}...**"
            )

            # Service Call
            service = DiplomacyService(session)
            success, vassal_data, player_vassals = await service.prepare_sea_levy_call(
                game.game_id,
                liege_discord_id=None,
                acting_house_id=target_house_id,
                is_gm_override=True,
            )

            if not success:
                return await player_wait_msg.edit(content="❌ Preparation failed.")

            if not vassal_data and not player_vassals:
                return await player_wait_msg.edit(content="❌ No naval vassals found.")

            # Notify Players
            if player_vassals:
                notified_count = 0
                for pv in player_vassals:
                    chan_name = f"{pv['house_name'].lower().replace(' ', '-')}-quarters"
                    channel = discord.utils.get(ctx.guild.text_channels, name=chan_name)
                    embed = discord.Embed(
                        title="🌊 A Call for Fleets! (GM Initiated)",
                        color=discord.Color.blue(),
                    )
                    embed.description = f"**{liege_house_obj.name}** calls the fleets to **{rally_point}**!"

                    try:
                        if channel:
                            await channel.send(f"<@{pv['discord_id']}>", embed=embed)
                            notified_count += 1
                        else:
                            member = await ctx.guild.fetch_member(pv["discord_id"])
                            if member:
                                await member.send(
                                    "⚠️ Naval Call (Channel missing)", embed=embed
                                )
                                notified_count += 1
                    except Exception:
                        pass
                await ctx.send(
                    f"📨 **Messages Sent:** Notified {notified_count} player vassals."
                )

            if not vassal_data:
                return await player_wait_msg.edit(
                    content="✅ **GM Call Sent!** (No NPC fleets)."
                )

            gm_channel = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")
            if not gm_channel:
                return await player_wait_msg.edit(content="❌ #gm-alerts missing.")

            # SAFE MAPPING
            vassal_data_for_db = [
                {
                    "house_id": v["house_id"],
                    "house_name": v["house_name"],
                    "max_ships": v.get("ships", 0),
                    "percent": v.get("percent", 0.0),
                    "home_x": v.get("home_x", 0),
                    "home_y": v.get("home_y", 0),
                }
                for v in vassal_data
            ]

            new_pending_call = PendingBannerCall(
                game_id=game.game_id,
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                message_id=player_wait_msg.id,
                gm_channel_id=gm_channel.id,
                gm_message_id=0,
                liege_house_id=target_house_id,
                rally_point_name=rally_point,
                vassal_data=vassal_data_for_db,
                call_type="SEA",
            )
            session.add(new_pending_call)
            await session.flush()

            view = BannerControlView(new_pending_call.id)
            # GM Initiated -> Pass ctx.author
            embed = await view.create_embed(
                pending_call=new_pending_call, gm_initiator=ctx.author
            )
            gm_panel_msg = await gm_channel.send(embed=embed, view=view)

            new_pending_call.gm_message_id = gm_panel_msg.id
            await session.commit()

            await player_wait_msg.edit(
                content=f"✅ **GM Naval Call Sent!** Review in #gm-alerts."
            )

    @gm_diplomacy.command(name="declare_fealty")
    @commands.check(is_gm)
    async def gm_declare_fealty(
        self, ctx, vassal_house_id: int, *, new_liege_name: str
    ):
        """GM: Make an NPC house declare fealty to another. Usage: !gm_diplomacy declare_fealty [VassalHouseID] [NewLiegeName]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = DiplomacyService(session)
            success, msg = await service.declare_fealty(
                game_id=game.game_id,
                vassal_house_id=vassal_house_id,
                new_liege_name=new_liege_name,
                is_gm_override=True,
            )

            # Post to declarations channel
            if success:
                dec_channel = discord.utils.get(
                    ctx.guild.text_channels, name="declarations"
                )
                if dec_channel:
                    embed = discord.Embed(
                        title="📜 GM Initiated Declaration of Fealty",
                        description=msg,
                        color=discord.Color.blue(),
                    )
                    await dec_channel.send(embed=embed)

            await ctx.send(f"✅ GM Command: {msg}")

    @gm_diplomacy.command(name="declare_war")
    @commands.check(is_gm)
    async def gm_declare_war(
        self,
        ctx,
        aggressor_house_id: int,
        target_house_name: str,
        *,
        reason: str = "Aggression",
    ):
        """GM: Make an NPC house declare war on another. Usage: !gm_diplomacy declare_war [AggressorHouseID] [TargetHouseName] [Reason='Aggression']"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            aggressor_house = await session.get(House, aggressor_house_id)
            if not aggressor_house:
                return await ctx.send(
                    f"❌ Aggressor House ID {aggressor_house_id} not found."
                )
            if aggressor_house.game_id != game.game_id:
                return await ctx.send(
                    f"❌ Aggressor House ID {aggressor_house_id} does not belong to this game."
                )

            dec_channel = discord.utils.get(
                ctx.guild.text_channels, name="declarations"
            )
            if dec_channel:
                embed = discord.Embed(
                    title="⚔️ GM Initiated Declaration of War", color=discord.Color.red()
                )
                embed.description = f"On behalf of **House {aggressor_house.name}**, GM {ctx.author.display_name} has declared WAR on **{target_house_name}**!"
                embed.add_field(name="Casus Belli", value=reason)
                await dec_channel.send(embed=embed)
                await ctx.send(
                    f"✅ GM Command: War declared on behalf of House {aggressor_house.name}."
                )
            else:
                await ctx.send("❌ Declarations channel not found.")


async def setup(bot):
    await bot.add_cog(DiplomacyCog(bot))
