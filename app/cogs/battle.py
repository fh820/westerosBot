import discord
from discord.ext import commands
from sqlalchemy import select
from app.db.db_manager import get_session
from app.db.repositories import GameRepo
from app.services.battle_service import BattleService
from app.ui.battle_view import BattleControlView
from app.db.models import Army, GamePlayer, User


class BattleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _notify_players_of_battle(
        self, session, game_id, army1_id, army2_id, battle_id, battle_url
    ):
        """Helper to find private quarters and notify players a battle has started."""
        # Find the owners of both armies
        stmt = (
            select(GamePlayer)
            .join(Army, Army.house_id == GamePlayer.claimed_house_id)
            .where(
                Army.army_id.in_([army1_id, army2_id]), GamePlayer.game_id == game_id
            )
        )
        players = (await session.execute(stmt)).scalars().all()

        for p in players:
            if p.private_channel_id:
                chan = self.bot.get_channel(p.private_channel_id)
                if chan:
                    embed = discord.Embed(
                        title="⚔️ Call to Arms!",
                        description=f"A battle involving your forces has begun!\n\n[**Jump to Battle Report**]({battle_url})",
                        color=discord.Color.red(),
                    )
                    embed.set_footer(text=f"Battle ID: {battle_id}")
                    try:
                        await chan.send(content=f"<@{p.user.discord_id}>", embed=embed)
                    except:
                        pass

    @commands.command(name="battle")
    @commands.has_permissions(administrator=True)
    async def begin_battle(
        self,
        ctx,
        attacker_id: int,
        defender_id: int,
        ambush: str = "none",
        defense: str = "none",
    ):
        """Starts a FIELD battle."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = BattleService(session)
            battle, odds_msg, calc_log = await service.start_battle(
                game.game_id, attacker_id, defender_id, ambush, defense
            )

            if not battle:
                return await ctx.send(odds_msg)

            # Lookup IC channels (Still by name for now, as these are server-wide)
            public_chan = discord.utils.get(
                ctx.guild.text_channels, name="battle-reports"
            )
            gm_chan = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")

            if not public_chan or not gm_chan:
                return await ctx.send("❌ #battle-reports or #gm-alerts missing.")

            view = BattleControlView(battle.id)
            public_embed, calc_embed = await view.generate_initial_embeds(
                session, calc_log=calc_log
            )
            public_msg = await public_chan.send(embed=public_embed, view=view)

            if calc_embed:
                await gm_chan.send(
                    content=f"🧮 **Battle ID {battle.id} Details:**", embed=calc_embed
                )

            # Store IDs for the View to use later
            battle.public_channel_id = public_chan.id
            battle.public_message_id = public_msg.id
            battle.gm_channel_id = gm_chan.id

            # NEW: Notify players in their LOCKED private quarters
            await self._notify_players_of_battle(
                session,
                game.game_id,
                attacker_id,
                defender_id,
                battle.id,
                public_msg.jump_url,
            )

            await session.commit()
            await ctx.send(f"✅ Battle started in {public_chan.mention}.")

    @commands.command(name="siege")
    @commands.has_permissions(administrator=True)
    async def begin_siege(self, ctx, attacker_id: int, *, fief_name: str):
        """Starts a SIEGE."""
        defense = "minor"
        if "defense=" in fief_name.lower():
            parts = fief_name.split("defense=")
            fief_name, defense = parts[0].strip(), parts[1].strip()
        fief_name = fief_name.strip(' "')

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = BattleService(session)
            battle, odds_msg, calc_log = await service.start_siege(
                game.game_id, attacker_id, fief_name, defense
            )

            if not battle:
                return await ctx.send(odds_msg)

            public_chan = discord.utils.get(
                ctx.guild.text_channels, name="battle-reports"
            )
            gm_chan = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")

            if not public_chan or not gm_chan:
                return await ctx.send("❌ Channels missing.")

            view = BattleControlView(battle.id)
            public_embed, calc_embed = await view.generate_initial_embeds(
                session, calc_log=calc_log
            )
            public_msg = await public_chan.send(embed=public_embed, view=view)

            if calc_embed:
                await gm_chan.send(
                    content=f"🧮 **Siege ID {battle.id} Details:**", embed=calc_embed
                )

            battle.public_channel_id = public_chan.id
            battle.public_message_id = public_msg.id
            battle.gm_channel_id = gm_chan.id

            # Notify defender (Finding house by fief name)
            # This is optional but good for immersion
            await session.commit()
            await ctx.send(f"✅ Siege started in {public_chan.mention}.")

    @commands.command(name="resolve_siege")
    @commands.has_permissions(administrator=True)
    async def resolve_siege_cmd(self, ctx, battle_id: int):
        """Resolves a won siege and notifies the Realm."""
        async with get_session() as session:
            service = BattleService(session)
            success, msg = await service.resolve_siege_consequences(battle_id)

            if success:
                news = discord.utils.get(
                    ctx.guild.text_channels, name="news-and-events"
                )
                if news:
                    await news.send(
                        embed=discord.Embed(
                            title="🏰 Siege Result",
                            description=msg,
                            color=discord.Color.gold(),
                        )
                    )
                await ctx.send("✅ Siege consequences applied.")
            else:
                await ctx.send(f"❌ {msg}")


async def setup(bot):
    await bot.add_cog(BattleCog(bot))
