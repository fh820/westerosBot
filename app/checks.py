from discord.ext import commands
from sqlalchemy import select
from app.db.db_manager import get_session
from app.db.models import Game, GamePlayer, User
from sqlalchemy.orm import selectinload
from app.db.repositories import GameRepo


async def is_in_house_channel(ctx):
    # GMs bypass checks
    if ctx.author.guild_permissions.administrator:
        return True
    # Allowed utility channels
    if ctx.channel.name in ["bot-testing", "gm-requests", "bot-commands"]:
        return True

    async with get_session() as session:
        # Check for locked ID in DB
        stmt = (
            select(GamePlayer)
            .join(User)
            .where(
                User.discord_id == ctx.author.id,
                GamePlayer.private_channel_id == ctx.channel.id,
            )
        )
        res = await session.execute(stmt)
        if res.scalars().first():
            return True

        if ctx.channel.name.endswith("-quarters"):
            return True

    return False


async def recruitment_is_enabled(ctx: commands.Context) -> bool:
    """
    A custom check that verifies if the recruitment system is enabled in the game rules
    by checking the 'manpower_enabled' flag. GMs can always bypass this.
    """
    if ctx.author.guild_permissions.administrator:
        return True

    async with get_session() as session:
        game = await GameRepo.get_active_game(session, ctx.guild.id)

        if not game or not game.manpower_enabled:
            return False

        return True
