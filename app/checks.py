from discord.ext import commands
from sqlalchemy import select
from app.db.db_manager import get_session
from app.db.models import Game, GamePlayer, User
from sqlalchemy.orm import selectinload
from app.db.repositories import GameRepo
from app.services.common import slugify

# async def is_in_house_channel(ctx: commands.Context) -> bool:
#     """
#     A custom check to ensure a command is run in the user's private house channel.
#     GMs (Administrators) can run the command anywhere.
#     """
#     # 1. GMs can bypass this check entirely.
#     if ctx.author.guild_permissions.administrator:
#         return True

#     async with get_session() as session:
#         # 2. Find the user's in-game house.
#         stmt = (
#             select(GamePlayer)
#             .join(User)
#             .where(User.discord_id == ctx.author.id)
#             .options(selectinload(GamePlayer.house))
#         )
#         player = (await session.execute(stmt)).scalars().first()

#         if not player or not player.house:
#             # If the user has no house, they can't use these commands anyway.
#             return False

#         # 3. Determine the correct channel name.
#         # This MUST match the logic you use to create the channels.
#         expected_channel_name = (
#             f"{player.house.name.lower().replace(' ', '-')}-quarters"
#         )


async def is_in_house_channel(ctx: commands.Context) -> bool:
    print(f"\n--- CHECK: is_in_house_channel for {ctx.author.name} ---")

    if ctx.author.guild_permissions.administrator:
        return True

    if ctx.channel.name in ["bot-testing", "gm-requests"]:
        return True

    async with get_session() as session:
        # IMPORTANT: Filter by the active game so we don't get 'Rhaegar' from a past session
        # We assume you have a way to get the current game_id,
        # similar to how you do it in the approve command.
        from app.db.models import Game  # Adjust import as needed

        # Get the active game for this server
        game_stmt = select(Game).where(
            Game.guild_id == ctx.guild.id, Game.is_active == True
        )
        game = (await session.execute(game_stmt)).scalars().first()

        if not game:
            return False

        stmt = (
            select(GamePlayer)
            .join(User)
            .where(User.discord_id == ctx.author.id)
            .where(GamePlayer.game_id == game.game_id)  # Filter by active game!
            .options(
                selectinload(GamePlayer.house),
                selectinload(GamePlayer.character),
            )
        )
        player = (await session.execute(stmt)).scalars().first()

        if not player:
            print("[DEBUG] No player record found for this active game.")
            return False

        actual_channel_name = ctx.channel.name

        # Use slugify to match Discord's channel format
        valid_slugs = []
        if player.house:
            valid_slugs.append(f"{slugify(player.house.name)}-quarters")
        if player.character:
            valid_slugs.append(f"{slugify(player.character.name)}-quarters")

        print(f"[DEBUG] Valid Slugs for {ctx.author.name}: {valid_slugs}")
        print(f"[DEBUG] Actual Channel: '{actual_channel_name}'")

        result = actual_channel_name in valid_slugs
        print(f"--- RESULT: {result} --- \n")
        return result


async def recruitment_is_enabled(ctx: commands.Context) -> bool:
    """
    A custom check that verifies if the recruitment system is enabled in the game rules
    by checking the 'manpower_enabled' flag. GMs can always bypass this.
    """
    if ctx.author.guild_permissions.administrator:
        return True

    async with get_session() as session:
        game = await GameRepo.get_active_game(session, ctx.guild.id)

        # --- THIS IS THE ONLY LINE THAT CHANGES ---
        # It now checks 'manpower_enabled' instead of the deleted 'recruitment_enabled'
        if not game or not game.manpower_enabled:
            return False

        return True
