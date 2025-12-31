# import discord
# from discord.ext import commands
# from sqlalchemy import select
# from app.db.db_manager import get_session
# from app.db.repositories import GameRepo
# from app.services.battle_service import BattleService
# from app.ui.battle_view import BattleControlView


# class BattleCog(commands.Cog):
#     def __init__(self, bot):
#         self.bot = bot

#     @commands.command(name="battle")
#     @commands.has_permissions(administrator=True)
#     async def begin_battle(
#         self,
#         ctx,
#         attacker_id: int,
#         defender_id: int,
#         ambush: str = "none",
#         defense: str = "none",
#     ):
#         """Starts a FIELD battle."""
#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await ctx.send("❌ No active game.")

#             service = BattleService(session)
#             battle, odds_msg, calc_log = await service.start_battle(
#                 game.game_id, attacker_id, defender_id, ambush, defense
#             )

#             if not battle:
#                 return await ctx.send(odds_msg)

#             public_chan = discord.utils.get(
#                 ctx.guild.text_channels, name="battle-reports"
#             )
#             gm_chan = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")

#             if not public_chan or not gm_chan:
#                 return await ctx.send("❌ Channels missing.")

#             view = BattleControlView(battle.id)
#             public_embed, calc_embed = await view.generate_initial_embeds(
#                 session, calc_log=calc_log
#             )

#             # 1. Send Public Panel (With Buttons)
#             public_msg = await public_chan.send(embed=public_embed, view=view)

#             # 2. Send Math to GM Channel
#             if calc_embed:
#                 await gm_chan.send(
#                     content=f"🧮 **Battle ID {battle.id} Details:**", embed=calc_embed
#                 )

#             # Save IDs (We track the public message now)
#             battle.public_channel_id = public_chan.id
#             battle.public_message_id = public_msg.id
#             battle.gm_channel_id = gm_chan.id  # Just for record keeping

#             await session.commit()
#             await ctx.send(
#                 f"✅ Battle started in {public_chan.mention}. Math sent to {gm_chan.mention}."
#             )

#     @commands.command(name="siege")
#     @commands.has_permissions(administrator=True)
#     async def begin_siege(self, ctx, attacker_id: int, *, fief_name: str):
#         """Starts a SIEGE."""
#         defense = "minor"

#         # Check for optional defense parameter
#         if "defense=" in fief_name.lower():
#             parts = fief_name.split("defense=")
#             fief_name = parts[0].strip()
#             defense = parts[1].strip()

#         # FIX: Only strip quotes from the start/end. Do NOT use .replace("'", "")
#         fief_name = fief_name.strip(' "')

#         async with get_session() as session:
#             game = await GameRepo.get_active_game(session, ctx.guild.id)
#             if not game:
#                 return await ctx.send("❌ No active game.")

#             service = BattleService(session)
#             battle, odds_msg, calc_log = await service.start_siege(
#                 game.game_id, attacker_id, fief_name, defense
#             )

#             if not battle:
#                 return await ctx.send(odds_msg)

#             public_chan = discord.utils.get(
#                 ctx.guild.text_channels, name="battle-reports"
#             )
#             gm_chan = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")

#             if not public_chan or not gm_chan:
#                 return await ctx.send("❌ Channels missing.")

#             view = BattleControlView(battle.id)
#             public_embed, calc_embed = await view.generate_initial_embeds(
#                 session, calc_log=calc_log
#             )

#             # 1. Send Public Panel
#             public_msg = await public_chan.send(embed=public_embed, view=view)

#             # 2. Send Math to GM
#             if calc_embed:
#                 await gm_chan.send(
#                     content=f"🧮 **Siege ID {battle.id} Details:**", embed=calc_embed
#                 )

#             battle.public_channel_id = public_chan.id
#             battle.public_message_id = public_msg.id
#             battle.gm_channel_id = gm_chan.id

#             await session.commit()
#             await ctx.send(f"✅ Siege started in {public_chan.mention}.")


#     @commands.command(name="resolve_siege")
#     @commands.has_permissions(administrator=True)
#     async def resolve_siege_cmd(self, ctx, battle_id: int):
#         async with get_session() as session:
#             service = BattleService(session)
#             success, msg = await service.resolve_siege_consequences(battle_id)

#             if success:
#                 news = discord.utils.get(
#                     ctx.guild.text_channels, name="news-and-events"
#                 )
#                 if news:
#                     await news.send(
#                         embed=discord.Embed(
#                             title="🏰 Siege Result",
#                             description=msg,
#                             color=discord.Color.gold(),
#                         )
#                     )
#                 await ctx.send("✅ Siege consequences applied.")
#             else:
#                 await ctx.send(msg)


# async def setup(bot):
#     await bot.add_cog(BattleCog(bot))
