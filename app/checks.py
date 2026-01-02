from discord.ext import commands
from sqlalchemy import select
from app.db.db_manager import get_session
from app.db.models import Game, GamePlayer, User
from sqlalchemy.orm import selectinload
from app.db.repositories import GameRepo


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

#         # 4. Compare with the current channel and return the result.
#         return ctx.channel.name == expected_channel_name


# In app/checks.py
async def is_in_house_channel(ctx: commands.Context) -> bool:
    print("\n--- CHECK: is_in_house_channel ---")

    # 1. Admin Bypass
    if ctx.author.guild_permissions.administrator:
        return True

    # 2. Database Lookup
    async with get_session() as session:
        # Load BOTH house and character
        stmt = (
            select(GamePlayer)
            .join(User)
            .where(User.discord_id == ctx.author.id)
            .options(
                selectinload(GamePlayer.house),
                selectinload(GamePlayer.character),  # Make sure to load the character!
            )
        )
        player = (await session.execute(stmt)).scalars().first()

        if not player:
            return False

        # 3. Logic: Check against both House Name AND Character Name
        # This allows them to use their personal quarters OR the main house quarters
        actual_channel_name = ctx.channel.name

        possible_names = []
        if player.house:
            possible_names.append(player.house.name.lower().replace(" ", "-"))
        if player.character:
            possible_names.append(player.character.name.lower().replace(" ", "-"))

        # Check if the current channel matches "[name]-quarters" for either house or character
        valid_channels = [f"{name}-quarters" for name in possible_names]

        print(f"[DEBUG] Valid Channels: {valid_channels}")
        print(f"[DEBUG] Actual Channel: '{actual_channel_name}'")

        result = actual_channel_name in valid_channels
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
