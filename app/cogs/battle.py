import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.db_manager import get_session
from app.db.repositories import GameRepo
from app.services.battle_service import BattleService
from app.ui.battle_view import BattleControlView
from app.db.models import Army, Battle, GamePlayer, User


class BattleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _refresh_battle_panel(self, session, battle_id: int, guild=None):
        battle = await session.get(Battle, battle_id)
        if not battle:
            return False

        channel = None
        if battle.public_channel_id:
            channel = self.bot.get_channel(battle.public_channel_id)
        if not channel and guild:
            channel = discord.utils.get(guild.text_channels, name="battle-reports")
        if not channel:
            return False

        view = BattleControlView(battle_id)
        embed, _ = await view.generate_initial_embeds(session)
        if not embed:
            return False

        message = None
        if battle.public_message_id:
            try:
                message = await channel.fetch_message(battle.public_message_id)
            except discord.HTTPException:
                message = None

        try:
            if message:
                await message.edit(embed=embed, view=view)
            else:
                message = await channel.send(embed=embed, view=view)
                battle.public_channel_id = channel.id
                battle.public_message_id = message.id
                await session.commit()
            return True
        except discord.HTTPException:
            return False

    async def _can_set_battle_side(self, ctx, session, battle_id: int, side: str):
        if ctx.author.guild_permissions.administrator:
            return True, None

        game = await GameRepo.get_active_game(session, ctx.guild.id)
        if not game:
            return False, "❌ No active game."

        stmt = (
            select(Battle)
            .where(Battle.id == battle_id)
            .options(selectinload(Battle.attacker), selectinload(Battle.defender))
        )
        battle = (await session.execute(stmt)).scalars().first()
        if not battle:
            return False, "❌ Battle not found."

        player = (
            await session.execute(
                select(GamePlayer)
                .join(User, User.user_id == GamePlayer.user_id)
                .where(
                    User.discord_id == ctx.author.id,
                    GamePlayer.game_id == game.game_id,
                )
            )
        ).scalars().first()
        if not player or not player.claimed_house_id:
            return False, "❌ You do not control a house."

        side_key = (side or "").lower()
        if side_key in ("attacker", "attack", "att"):
            required_house_id = battle.attacker.house_id if battle.attacker else None
        elif side_key in ("defender", "defense", "def"):
            required_house_id = battle.defender.house_id if battle.defender else None
        else:
            return False, "❌ Side must be attacker or defender."

        if player.claimed_house_id != required_house_id:
            return False, "❌ You do not control that side of this battle."
        return True, None

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
        terrain: str = "unknown",
    ):
        """Starts a FIELD battle."""
        if ambush.lower().startswith("terrain="):
            terrain = ambush.split("=", 1)[1]
            ambush = "none"
        if defense.lower().startswith("terrain="):
            terrain = defense.split("=", 1)[1]
            defense = "none"
        if terrain.lower().startswith("terrain="):
            terrain = terrain.split("=", 1)[1]

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = BattleService(session)
            battle, odds_msg, calc_log = await service.start_battle(
                game.game_id, attacker_id, defender_id, ambush, defense, terrain
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
    @commands.command(
        name="refresh_battle",
        aliases=["refresh-battle", "refresh_siege", "refresh-siege"],
    )
    @commands.has_permissions(administrator=True)
    async def refresh_battle_cmd(self, ctx, battle_id: int):
        """Refreshes or reposts a battle/siege control panel."""
        async with get_session() as session:
            refreshed = await self._refresh_battle_panel(
                session, battle_id, guild=ctx.guild
            )
            if refreshed:
                await ctx.send(f"OK Refreshed battle/siege panel {battle_id}.")
            else:
                await ctx.send(
                    "ERROR Could not refresh that panel. Check the battle ID and #battle-reports."
                )

    @commands.command(name="battle_plan", aliases=["battle-plan"])
    async def battle_plan_cmd(self, ctx, battle_id: int, side: str, plan: str):
        """Sets the next tactical plan for a field battle side."""
        async with get_session() as session:
            allowed, err = await self._can_set_battle_side(ctx, session, battle_id, side)
            if not allowed:
                return await ctx.send(err)

            service = BattleService(session)
            success, msg = await service.set_field_plan(battle_id, side, plan)
            if success:
                await self._refresh_battle_panel(session, battle_id)
            prefix = "✅" if success else "❌"
            await ctx.send(f"{prefix} {msg}")

    @commands.command(name="battle_terrain", aliases=["battle-terrain"])
    @commands.has_permissions(administrator=True)
    async def battle_terrain_cmd(self, ctx, battle_id: int, terrain: str):
        """Sets terrain for a battle."""
        async with get_session() as session:
            service = BattleService(session)
            success, msg = await service.set_battle_terrain(battle_id, terrain)
            if success:
                await self._refresh_battle_panel(session, battle_id)
            prefix = "✅" if success else "❌"
            await ctx.send(f"{prefix} {msg}")

    @commands.command(name="siege_action")
    async def siege_action_cmd(self, ctx, battle_id: int, side: str, action: str):
        """Sets the next attacker or defender action for a siege turn."""
        async with get_session() as session:
            allowed, err = await self._can_set_battle_side(ctx, session, battle_id, side)
            if not allowed:
                return await ctx.send(err)

            service = BattleService(session)
            success, msg = await service.set_siege_action(battle_id, side, action)
            if success:
                await self._refresh_battle_panel(session, battle_id)
            prefix = "✅" if success else "❌"
            await ctx.send(f"{prefix} {msg}")

    @commands.command(name="blockade")
    @commands.has_permissions(administrator=True)
    async def blockade_cmd(self, ctx, fleet_id: int, battle_id: int):
        """Attaches a fleet to an active siege as a blockade."""
        async with get_session() as session:
            service = BattleService(session)
            success, msg = await service.attach_blockade(battle_id, fleet_id)
            if success:
                await self._refresh_battle_panel(session, battle_id)
            prefix = "✅" if success else "❌"
            await ctx.send(f"{prefix} {msg}")


async def setup(bot):
    await bot.add_cog(BattleCog(bot))
