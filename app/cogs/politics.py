import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import re
import datetime

from app.db.db_manager import get_session
from app.db.models import House, Game, GamePlayer, User, Fief, Army, Character
from app.db.repositories import GameRepo


class PoliticsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _notify_private_quarters(self, session, game_id, discord_id, embed):
        """Helper to send an embed to a player's locked private channel."""
        stmt = (
            select(GamePlayer)
            .join(User)
            .where(User.discord_id == discord_id, GamePlayer.game_id == game_id)
        )
        player = (await session.execute(stmt)).scalars().first()

        if player and player.private_channel_id:
            chan = self.bot.get_channel(player.private_channel_id)
            if chan:
                try:
                    await chan.send(content=f"<@{discord_id}>", embed=embed)
                except:
                    pass

    # --- GM TOOL ---
    @commands.command(name="coronate")
    @commands.has_permissions(administrator=True)
    async def coronate(self, ctx, target: discord.Member):
        """GM Only: Assigns the Iron Throne and notifies the new King's quarters."""
        role = discord.utils.get(ctx.guild.roles, name="IronThrone")
        if not role:
            return await ctx.send("❌ Role `IronThrone` not found.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)

            # 1. Role Management
            for member in ctx.guild.members:
                if role in member.roles:
                    await member.remove_roles(role)
            await target.add_roles(role)

            # 2. Announcement
            msg = (
                f"👑 **All Hail His Grace!** {target.mention} now sits the Iron Throne."
            )
            decree_channel = discord.utils.get(
                ctx.guild.text_channels, name="royal-decrees"
            )
            if decree_channel:
                await decree_channel.send(msg)

            # 3. Private Notification via ID
            if game:
                embed = discord.Embed(
                    title="👑 Coronation", description=msg, color=discord.Color.gold()
                )
                await self._notify_private_quarters(
                    session, game.game_id, target.id, embed
                )

            await ctx.send(f"✅ Coronation complete.")

    # --- KING TOOLS ---
    @commands.command(name="appoint")
    async def appoint_council(self, ctx, target: discord.Member, *, title: str):
        """King Only: Appoint a Small Council member and notify their quarters."""
        king_role = discord.utils.get(ctx.guild.roles, name="IronThrone")
        if (
            king_role not in ctx.author.roles
            and not ctx.author.guild_permissions.administrator
        ):
            return await ctx.send("❌ You do not sit the Iron Throne.")

        valid_titles = [
            "Hand of the King",
            "Master of Coin",
            "Master of Whisperers",
            "Master of Ships",
            "Master of Laws",
            "Lord Commander",
            "Grand Maester",
        ]

        selected_title = next(
            (t for t in valid_titles if t.lower() == title.lower()), None
        )
        if not selected_title:
            return await ctx.send(
                f"❌ Invalid Title. Choose from: {', '.join(valid_titles)}"
            )

        title_role = discord.utils.get(ctx.guild.roles, name=selected_title)
        access_role = discord.utils.get(ctx.guild.roles, name="SmallCouncil")

        if not title_role or not access_role:
            return await ctx.send("❌ Council roles are missing from the server.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            await target.add_roles(title_role, access_role)

            # 1. Global Announcement
            msg = f"📜 **Royal Decree:** His Grace appoints {target.mention} as **{selected_title}**."
            decree_channel = discord.utils.get(
                ctx.guild.text_channels, name="royal-decrees"
            )
            if decree_channel:
                await decree_channel.send(msg)

            # 2. Private Notification via ID
            if game:
                embed = discord.Embed(
                    title="🦅 Council Appointment",
                    description=msg,
                    color=discord.Color.blue(),
                )
                await self._notify_private_quarters(
                    session, game.game_id, target.id, embed
                )

            await ctx.send(f"✅ Appointed {target.display_name} to the council.")

    @commands.command(name="grant_title")
    async def grant_title(self, ctx, *, input_str: str):
        """Grants a Fief and its garrison to another player. Notifies recipient quarters."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Identify Sender
            stmt_sender = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )
            sender_p = (await session.execute(stmt_sender)).scalars().first()
            if not sender_p or not sender_p.is_primary:
                return await ctx.send("❌ Only House Heads can grant titles.")

            # 2. Parsing Fief and Target
            stmt_fiefs = select(Fief).where(Fief.owner_id == sender_p.claimed_house_id)
            owned_fiefs = (await session.execute(stmt_fiefs)).scalars().all()
            owned_fiefs.sort(key=lambda x: len(x.name), reverse=True)

            target_fief, target_name_raw = None, ""
            for f in owned_fiefs:
                if input_str.lower().startswith(f.name.lower()):
                    target_fief = f
                    target_name_raw = input_str[len(f.name) :].strip()
                    break

            if not target_fief or not target_name_raw:
                return await ctx.send(
                    "❌ Usage: `!grant_title [Castle Name] [@User or Character Name]`"
                )

            # 3. Resolve Recipient (Target)
            target_discord_id = None
            mention_match = re.search(r"<@!?(\d+)>", target_name_raw)

            if mention_match:
                target_discord_id = int(mention_match.group(1))
            else:
                # Character Name Lookup
                stmt_char = (
                    select(GamePlayer)
                    .join(Character)
                    .join(User)
                    .where(
                        Character.name.ilike(target_name_raw),
                        GamePlayer.game_id == game.game_id,
                    )
                )
                res = (await session.execute(stmt_char)).scalars().first()
                if res:
                    target_discord_id = (
                        await session.get(User, res.user_id)
                    ).discord_id

            if not target_discord_id:
                return await ctx.send(
                    f"❌ Could not find player/character: **{target_name_raw}**"
                )

            stmt_target_gp = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == target_discord_id,
                    GamePlayer.game_id == game.game_id,
                )
                .options(selectinload(GamePlayer.house))
            )
            target_p = (await session.execute(stmt_target_gp)).scalars().first()

            if not target_p or not target_p.claimed_house_id:
                return await ctx.send("❌ Recipient must have a claimed house.")

            # 4. EXECUTE TRANSFER
            old_house_id = sender_p.claimed_house_id
            new_house_id = target_p.claimed_house_id

            target_fief.owner_id = new_house_id

            # Move Garrisons
            stmt_army = select(Army).where(
                Army.house_id == old_house_id,
                Army.location_x == target_fief.location_x,
                Army.location_y == target_fief.location_y,
                Army.status == "GARRISONED",
            )
            for army in (await session.execute(stmt_army)).scalars().all():
                army.house_id = new_house_id

            # Set Liege-Vassal Relationship
            target_p.house.liege_id = old_house_id

            # Small gold transfer for "upkeep"
            transfer_amt = target_fief.base_income * 2
            sender_house = await session.get(House, old_house_id)
            if sender_house.treasury >= transfer_amt:
                sender_house.treasury -= transfer_amt
                target_p.house.treasury += transfer_amt
                gold_msg = f"with **{transfer_amt} Gold**."
            else:
                gold_msg = "with empty coffers."

            await session.commit()

            # 5. NOTIFY RECIPIENT QUARTERS via Locked ID
            proclamation = f"📜 **Royal Proclamation:** **{target_fief.name}** has been granted to your House {gold_msg}"
            embed = discord.Embed(
                title="🏰 Title Granted",
                description=proclamation,
                color=discord.Color.green(),
            )
            await self._notify_private_quarters(
                session, game.game_id, target_discord_id, embed
            )

            await ctx.send(
                f"✅ **Proclamation dispatched:** {target_fief.name} is now held by {target_p.house.name}."
            )


async def setup(bot):
    await bot.add_cog(PoliticsCog(bot))
