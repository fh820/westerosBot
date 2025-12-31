import discord
from discord.ext import commands
from sqlalchemy import select, update, delete

# NEW, CORRECTED LINE
from app.db.db_manager import get_session
from app.db.models import (
    House,
    Game,
    GamePlayer,
    User,
    Fief,
    Army,
    ArmyContingent,
    MarchLog,
    Character,
    Battle,
    PendingBannerCall,
    PendingInteraction,
)
from app.services.setup_service import SetupService
from app.services.scenario_service import ScenarioService

# from app.services.warfare_service import WarfareService
from app.db.repositories import GameRepo
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from app.services.warfare_service import WarfareService


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def is_gm(self, ctx):
        return ctx.author.guild_permissions.administrator

    async def ensure_roles(self, guild):
        """Creates necessary game roles if they are missing."""
        roles_needed = [
            ("IronThrone", discord.Color.gold()),
            ("SmallCouncil", discord.Color.dark_grey()),
            ("Hand of the King", discord.Color.gold()),
            ("Master of Coin", discord.Color.yellow()),
            ("Master of Whisperers", discord.Color.purple()),
            ("Master of Ships", discord.Color.dark_blue()),
            ("Master of Laws", discord.Color.dark_orange()),
            ("Lord Commander", discord.Color.from_rgb(255, 255, 255)),
            ("Grand Maester", discord.Color.light_grey()),
        ]

        for name, color in roles_needed:
            role = discord.utils.get(guild.roles, name=name)
            if not role:
                try:
                    await guild.create_role(name=name, mentionable=True, color=color)
                except:
                    pass

    async def create_logistics_channels(self, ctx):
        guild = ctx.guild
        await self.ensure_roles(guild)
        role_it = discord.utils.get(guild.roles, name="IronThrone")
        role_sc = discord.utils.get(guild.roles, name="SmallCouncil")

        public_read_only = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=True, send_messages=False
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
        }

        public_read_write = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
        }

        gm_request_perms = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=True, send_messages=False
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
        }
        private_gm = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
            # Note: Server Administrators bypass channel permissions automatically on Discord.
        }

        structure = {
            "Game Logistics": [
                ("claims", public_read_write),
                ("gm-alerts", private_gm),
                ("gm-requests", gm_request_perms),
            ],
            "In Character": [
                ("news-and-events", public_read_only),
                ("battle-rumours", public_read_only),
                ("battle-reports", public_read_only),
                ("rumours", public_read_only),
                ("marriages", public_read_only),
                ("declarations", public_read_only),
                ("general-movements", public_read_only),
                ("army-movements", public_read_only),
                (
                    "royal-decrees",
                    {
                        guild.default_role: discord.PermissionOverwrite(
                            read_messages=True, send_messages=False
                        ),
                        role_it: discord.PermissionOverwrite(send_messages=True),
                        guild.me: discord.PermissionOverwrite(
                            read_messages=True, send_messages=True
                        ),
                    },
                ),
                ("ravens-n-scrolls", public_read_write),
                (
                    "small-council",
                    {
                        guild.default_role: discord.PermissionOverwrite(
                            read_messages=False
                        ),
                        role_it: discord.PermissionOverwrite(
                            read_messages=True, send_messages=True
                        ),
                        role_sc: discord.PermissionOverwrite(
                            read_messages=True, send_messages=True
                        ),
                        guild.me: discord.PermissionOverwrite(
                            read_messages=True, send_messages=True
                        ),
                    },
                ),
                ("westeros-ic", public_read_write),
                ("kingslanding", public_read_write),
                ("open-to-do-list", public_read_write),
                ("tournament", public_read_only),
            ],
        }

        created_count = 0
        for cat_name, channels in structure.items():
            category = discord.utils.get(guild.categories, name=cat_name)
            if not category:
                category = await guild.create_category(cat_name)

            for chan_name, overwrites in channels:
                existing = discord.utils.get(guild.text_channels, name=chan_name)
                if not existing:
                    await guild.create_text_channel(
                        name=chan_name, category=category, overwrites=overwrites
                    )
                    created_count += 1
        return created_count

    # @commands.command()
    # @commands.has_permissions(administrator=True)
    # async def setup_game(self, ctx, ruling_house: str = "Targaryen"):
    #     async with get_session() as session:
    #         stmt = select(Game).where(Game.guild_id == ctx.guild.id)
    #         result = await session.execute(stmt)
    #         existing_game = result.scalars().first()

    #         if existing_game:
    #             await ctx.send("⚠️ Game Active. Use `!end_game CONFIRM PURGE`.")
    #             return

    #     msg = await ctx.send(f"🌍 **Initializing World...** (Crown: {ruling_house})")

    #     async with get_session() as session:
    #         setup = SetupService(session)
    #         success, message = await setup.init_world(
    #             ctx.guild.id, "master_world_data.json", ruling_house
    #         )

    #         if success:
    #             await msg.edit(content=f"{message}\n🔨 **Constructing Channels...**")
    #             count = await self.create_logistics_channels(ctx)
    #             await ctx.send(f"✅ **Ready.** Created {count} channels.")
    #         else:
    #             await ctx.send(f"⚠️ Setup Failed: {message}")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setup_game(self, ctx, ruling_house: str = "Targaryen"):
        async with get_session() as session:
            stmt = select(Game).where(Game.guild_id == ctx.guild.id)
            result = await session.execute(stmt)
            existing_game = result.scalars().first()

            if existing_game:
                await ctx.send("⚠️ Game Active. Use `!end_game CONFIRM PURGE`.")
                return

        msg = await ctx.send(f"🌍 **Initializing World...** (Crown: {ruling_house})")

        async with get_session() as session:
            setup = SetupService(session)
            # --- FIX: Pass ctx.author.id to the service ---
            success, message = await setup.init_world(
                ctx.guild.id, ctx.author.id, "master_world_data.json", ruling_house
            )

            if success:
                await msg.edit(content=f"{message}\n🔨 **Constructing Channels...**")
                count = await self.create_logistics_channels(ctx)
                await ctx.send(f"✅ **Ready.** Created {count} channels.")
            else:
                await ctx.send(f"⚠️ Setup Failed: {message}")

    @commands.command(name="end_game")
    @commands.has_permissions(administrator=True)
    async def end_game(self, ctx, confirmation: str, mode: str = ""):
        """
        Wipes the game. Uses strict deletion order to prevent Database crashes.
        Usage: !end_game CONFIRM [PURGE]
        """
        if confirmation != "CONFIRM":
            await ctx.send("⚠️ Usage: `!end_game CONFIRM [PURGE]`")
            return

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)

            if not game and mode.upper() != "PURGE":
                await ctx.send("❌ No active game to end.")
                return

            try:
                msg = await ctx.send(f"🚨 **Ending Game...**")
            except:
                msg = None

            # 1. PURGE DISCORD ASSETS
            if mode.upper() == "PURGE":
                if msg:
                    try:
                        await msg.edit(
                            content=f"🚨 **Ending Game...** (Nuking Channels...)"
                        )
                    except:
                        pass

                # A. Delete Categories and Channels
                target_categories = [
                    "Game Logistics",
                    "In Character",
                    "Great Houses",
                    "Meetings",
                ]
                for cat_name in target_categories:
                    category = discord.utils.get(ctx.guild.categories, name=cat_name)
                    if category:
                        for channel in category.channels:
                            try:
                                await channel.delete()
                            except:
                                pass
                        try:
                            await category.delete()
                        except:
                            pass

                # B. Delete Roles (House & System)
                if game:
                    # House Roles
                    stmt = select(House).where(House.game_id == game.game_id)
                    houses = (await session.execute(stmt)).scalars().all()
                    for house in houses:
                        role = discord.utils.get(ctx.guild.roles, name=house.name)
                        if role:
                            try:
                                await role.delete()
                            except:
                                pass

                # System Roles
                sys_roles = [
                    "IronThrone",
                    "SmallCouncil",
                    "Hand of the King",
                    "Master of Coin",
                    "Master of Whisperers",
                    "Master of Ships",
                    "Master of Laws",
                    "Lord Commander",
                    "Grand Maester",
                ]
                for r_name in sys_roles:
                    r = discord.utils.get(ctx.guild.roles, name=r_name)
                    if r:
                        try:
                            await r.delete()
                        except:
                            pass

                try:
                    await ctx.send("🗑️ **Purge Complete.**")
                except:
                    pass

            # 2. DELETE DATABASE (STRICT ORDER FIX)
            if game:
                if msg:
                    try:
                        await msg.edit(
                            content=f"🚨 **Ending Game...** (Cleaning Database...)"
                        )
                    except:
                        pass

                # A. Break Circular Dependencies in Houses (Liege/Dynasty)
                await session.execute(
                    update(House)
                    .where(House.game_id == game.game_id)
                    .values(liege_id=None, dynasty_id=None)
                )
                await session.execute(
                    delete(Battle).where(Battle.game_id == game.game_id)
                )
                # B. Delete "Leaf" Tables (Data that depends on Houses/Game)
                await session.execute(
                    delete(MarchLog).where(MarchLog.game_id == game.game_id)
                )
                await session.execute(
                    delete(PendingInteraction).where(
                        PendingInteraction.game_id == game.game_id
                    )
                )
                await session.execute(
                    delete(PendingBannerCall).where(
                        PendingBannerCall.game_id == game.game_id
                    )
                )

                # Delete ArmyContingents (Linked to Armies)
                # (We use a subquery because ArmyContingent might not have game_id directly)
                await session.execute(
                    delete(ArmyContingent).where(
                        ArmyContingent.parent_army_id.in_(
                            select(Army.army_id).where(Army.game_id == game.game_id)
                        )
                    )
                )

                await session.execute(delete(Army).where(Army.game_id == game.game_id))
                await session.execute(delete(Fief).where(Fief.game_id == game.game_id))
                await session.execute(
                    delete(GamePlayer).where(GamePlayer.game_id == game.game_id)
                )

                # Delete Characters (Linked to Houses)
                await session.execute(
                    delete(Character).where(
                        Character.house_id.in_(
                            select(House.house_id).where(House.game_id == game.game_id)
                        )
                    )
                )

                # C. Delete Parent Tables
                await session.execute(
                    delete(House).where(House.game_id == game.game_id)
                )
                await session.delete(game)

                await session.commit()

                try:
                    await ctx.send("💥 **Game Over.** Campaign data wiped.")
                except:
                    pass
            else:
                try:
                    await ctx.send("💥 **Force Clean Complete.** (No DB record found).")
                except:
                    pass

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def set_head(self, ctx, target: discord.Member, house_name: str):
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt_h = select(House).where(
                House.name.ilike(house_name), House.game_id == game.game_id
            )
            house = (await session.execute(stmt_h)).scalars().first()
            if not house:
                await ctx.send(f"❌ House **{house_name}** not found.")
                return

            stmt_p = (
                select(GamePlayer)
                .join(User)
                .where(User.discord_id == target.id, GamePlayer.game_id == game.game_id)
            )
            player = (await session.execute(stmt_p)).scalars().first()

            if not player:
                await ctx.send(
                    f"❌ {target.display_name} must `!claim` a character first."
                )
                return

            stmt_old = select(GamePlayer).where(
                GamePlayer.claimed_house_id == house.house_id,
                GamePlayer.is_primary == True,
            )
            old_head = (await session.execute(stmt_old)).scalars().first()
            if old_head:
                old_head.is_primary = False

            player.claimed_house_id = house.house_id
            player.is_primary = True

            await session.commit()
            await ctx.send(
                f"👑 **Succession:** {target.mention} is now the **Head of House {house.name}**."
            )
            await ctx.send(
                f"ℹ️ {target.mention} retains their current quarters, but now controls the main Treasury."
            )

    @commands.command(name="vacate")
    @commands.has_permissions(administrator=True)
    async def vacate(self, ctx, target: discord.Member):
        """
        Removes a player's claim from a House or Character, turning it into an NPC.
        This includes a robust cleanup of all associated roles and channels.
        Usage: !vacate @User#1234
        """
        print("\n--- Running !vacate command ---")
        print(
            f"[DEBUG] Command initiated by: {ctx.author.name} for target: {target.name}"
        )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                print("[DEBUG] FAILED: No active game found.")
                return await ctx.send("❌ No active game.")

            print(f"[DEBUG] Active game found: {game.game_id}")

            # --- 1. Find the Player's Claim via their Discord ID ---
            print(f"[DEBUG] Searching for claim for Discord ID: {target.id}")
            stmt = (
                select(GamePlayer)
                .join(User)
                .where(
                    GamePlayer.game_id == game.game_id,
                    User.discord_id == target.id,
                )
                .options(
                    selectinload(GamePlayer.house).selectinload(House.dynasty),
                    selectinload(GamePlayer.character),
                )
            )
            player_claim = (await session.execute(stmt)).scalars().first()

            if not player_claim:
                print("[DEBUG] FAILED: No GamePlayer entry found in the database.")
                return await ctx.send(
                    f"❌ Database query found no active claim for {target.mention}."
                )

            # --- FIX IS HERE ---
            print(f"[DEBUG] Found GamePlayer claim. ID: {player_claim.id}")

            # --- The rest of the command remains the same ---
            claimed_house = player_claim.house
            claimed_char = player_claim.character

            # --- 2. Robust Discord Role & Channel Cleanup ---
            print("[DEBUG] Starting Discord asset cleanup...")
            roles_to_remove = []
            channels_to_delete = []

            # A: House Role (e.g., "Stark", "House Stark") - IMPROVED LOGIC
            if claimed_house:
                possible_names = {
                    claimed_house.name,
                    f"House {claimed_house.name}",
                    claimed_house.name.replace("House ", ""),
                }
                print(f"[DEBUG] Searching for House roles with names: {possible_names}")
                for name in possible_names:
                    if role := discord.utils.get(ctx.guild.roles, name=name):
                        roles_to_remove.append(role)

            # B: Dynasty Role (e.g., "The North")
            if claimed_house and claimed_house.dynasty:
                possible_names = {
                    claimed_house.dynasty.name,
                    claimed_house.dynasty.name.replace("The ", ""),
                }
                print(
                    f"[DEBUG] Searching for Dynasty roles with names: {possible_names}"
                )
                for name in possible_names:
                    if role := discord.utils.get(ctx.guild.roles, name=name):
                        roles_to_remove.append(role)

            # C: Character Role (e.g., "Robb Stark")
            if claimed_char:
                print(f"[DEBUG] Searching for Character role: {claimed_char.name}")
                if role := discord.utils.get(ctx.guild.roles, name=claimed_char.name):
                    roles_to_remove.append(role)

            # D: System & Title Roles
            system_roles = [
                "SmallCouncil",
                "Hand of the King",
                "Master of Coin",
                "Master of Ships",
                "Master of Whisperers",
                "Master of Laws",
                "Grand Maester",
                "Lord Commander",
            ]
            print(f"[DEBUG] Checking for system roles on user: {system_roles}")
            for role_name in system_roles:
                if role := discord.utils.get(ctx.guild.roles, name=role_name):
                    if role in target.roles:
                        roles_to_remove.append(role)

            # Remove duplicate roles and perform the removal
            if roles_to_remove:
                final_roles = list(set(roles_to_remove))
                role_names_str = ", ".join(f"'{r.name}'" for r in final_roles)
                print(
                    f"[DEBUG] Attempting to remove {len(final_roles)} roles: {role_names_str}"
                )
                try:
                    await target.remove_roles(
                        *final_roles, reason=f"Claim vacated by {ctx.author.name}"
                    )
                    print("[DEBUG] Role removal successful.")
                    await ctx.send(
                        f"🎖️ Roles removed from {target.mention}: {', '.join(f'`{r.name}`' for r in final_roles)}"
                    )
                except discord.Forbidden:
                    print(
                        "[DEBUG] FAILED: Role removal failed due to discord.Forbidden. BOT ROLE IS TOO LOW."
                    )
                    await ctx.send(
                        "⚠️ **Permissions Error:** The bot's role is too low to manage these roles. Please move the bot role higher."
                    )
                except discord.HTTPException as e:
                    print(
                        f"[DEBUG] FAILED: Role removal failed due to an API error: {e}"
                    )
                    await ctx.send(f"⚠️ An API error occurred while removing roles: {e}")

            # E: Find associated channels to delete
            is_house_head = not claimed_char or claimed_char.is_head
            if is_house_head and claimed_house:
                ch_name = f"{claimed_house.name.lower().replace(' ', '-')}-quarters"
                print(f"[DEBUG] Searching for House channel: #{ch_name}")
                if channel := discord.utils.get(ctx.guild.text_channels, name=ch_name):
                    channels_to_delete.append(channel)

            if claimed_char:
                ch_name = f"{claimed_char.name.lower().replace(' ', '-')}-quarters"
                print(f"[DEBUG] Searching for Character channel: #{ch_name}")
                if channel := discord.utils.get(ctx.guild.text_channels, name=ch_name):
                    channels_to_delete.append(channel)

            # Delete the channels
            if channels_to_delete:
                final_channels = list(set(channels_to_delete))
                channel_names_str = ", ".join(f"'#{c.name}'" for c in final_channels)
                print(
                    f"[DEBUG] Attempting to delete {len(final_channels)} channels: {channel_names_str}"
                )
                for channel in final_channels:
                    try:
                        await channel.delete(
                            reason=f"Claim vacated by {ctx.author.name}"
                        )
                        print(f"[DEBUG] Successfully deleted channel #{channel.name}")
                        await ctx.send(
                            f"🧹 Channel `#{channel.name}` has been deleted."
                        )
                    except discord.Forbidden:
                        print(
                            f"[DEBUG] FAILED: Could not delete channel #{channel.name} due to permissions."
                        )
                        await ctx.send(
                            f"⚠️ Lacking permissions to delete `#{channel.name}`."
                        )
                    except Exception as e:
                        print(
                            f"[DEBUG] FAILED: Could not delete channel #{channel.name}: {e}"
                        )
                        await ctx.send(
                            f"⚠️ Could not delete channel `#{channel.name}`: {e}"
                        )

            # --- 3. Database Update ---
            print("[DEBUG] Proceeding to database update.")
            try:
                # --- FIX IS HERE ---
                print(
                    f"[DEBUG] Issuing session.delete() for GamePlayer ID: {player_claim.id}"
                )
                await session.delete(player_claim)
                print("[DEBUG] Delete command issued. Attempting to commit...")
                await session.commit()
                print(
                    "[DEBUG] COMMIT SUCCESSFUL. Player has been removed from the database."
                )
                entity_name = claimed_char.name if claimed_char else claimed_house.name
                await ctx.send(
                    f"✅ **Vacate Complete.** {target.mention} has been removed from **{entity_name}**."
                )
            except Exception as e:
                print(
                    f"[DEBUG] FAILED: Database commit failed. The transaction was rolled back."
                )
                print(f"--- DATABASE ERROR --- \n{e}\n--- END ERROR ---")
                await ctx.send(
                    f"❌ **CRITICAL ERROR:** A database error occurred during the final step. The user's claim has NOT been removed. Error: {e}"
                )

            print("--- !vacate command finished ---\n")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def debug_me(self, ctx):
        """Checks how the database sees the person running the command."""
        async with get_session() as session:
            from app.db.models import Game, GamePlayer, User
            from sqlalchemy.orm import selectinload

            await ctx.send("🕵️ Running diagnostic...")

            user_id = ctx.author.id
            guild_id = ctx.guild.id

            await ctx.send(
                f"**Your Discord ID:** `{user_id}`\n**Server ID:** `{guild_id}`"
            )

            # 1. Find the active game
            game = await GameRepo.get_active_game(session, guild_id)
            if not game:
                return await ctx.send(
                    "❌ **CRITICAL:** No active game found for this server."
                )

            await ctx.send(f"✅ Found active game with **Game ID:** `{game.game_id}`")

            # 2. Try to find your GamePlayer record using the exact same logic as the service
            stmt = (
                select(GamePlayer)
                .join(User)
                .where(User.discord_id == user_id, GamePlayer.game_id == game.game_id)
                .options(selectinload(GamePlayer.user), selectinload(GamePlayer.house))
            )

            player_record = (await session.execute(stmt)).scalars().first()

            if player_record:
                house_name = player_record.house.name if player_record.house else "N/A"
                await ctx.send(
                    f"✅ **SUCCESS!** Found your `GamePlayer` record.\n"
                    f"**Claimed House:** `{house_name}`\n"
                    f"**Is Primary Player:** `{player_record.is_primary}`"
                )
            else:
                await ctx.send(
                    f"❌ **FAILURE!** The database query returned `None`.\n"
                    f"This is the root cause of the error. It means the bot cannot find a `GamePlayer` associated with your Discord ID for this specific game."
                )

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def set_crown(self, ctx, *, house_name: str):
        """Changes the Ruling House."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = select(House).where(
                House.game_id == game.game_id, House.name.ilike(house_name)
            )
            house = (await session.execute(stmt)).scalars().first()
            if not house:
                await ctx.send(f"❌ House **{house_name}** not found.")
                return
            game.ruling_house = house.name
            await session.commit()
            await ctx.send(f"👑 **The Crown** is now held by **House {house.name}**.")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def load_scenario(self, ctx, scenario_name: str):
        """Applies a historical patch."""
        async with get_session() as session:
            service = ScenarioService(session)
            await ctx.send(f"⏳ Loading scenario `{scenario_name}`...")
            success, msg = await service.load_scenario(ctx.guild.id, scenario_name)
            await ctx.send(msg)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def force_grant(self, ctx, castle_name: str, target: discord.Member):
        """GM Tool: Force transfer a fief."""
        async with get_session() as session:
            from app.db.models import Fief, Army

            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt_fief = select(Fief).where(
                Fief.name.ilike(castle_name), Fief.game_id == game.game_id
            )
            fief = (await session.execute(stmt_fief)).scalars().first()
            if not fief:
                await ctx.send(f"❌ Fief **{castle_name}** not found.")
                return

            stmt_target = (
                select(GamePlayer)
                .join(User)
                .where(User.discord_id == target.id, GamePlayer.game_id == game.game_id)
            )
            target_p = (await session.execute(stmt_target)).scalars().first()
            if not target_p or not target_p.claimed_house_id:
                await ctx.send(f"❌ Target has no claimed house.")
                return

            old_owner_id = fief.owner_id
            fief.owner_id = target_p.claimed_house_id

            stmt_army = select(Army).where(
                Army.house_id == old_owner_id,
                Army.location_x == fief.location_x,
                Army.location_y == fief.location_y,
                Army.status == "GARRISONED",
            )
            garrisons = (await session.execute(stmt_army)).scalars().all()
            for army in garrisons:
                army.house_id = target_p.claimed_house_id
                army.commander_name = f"Garrison of {fief.name}"

            await session.commit()
            await ctx.send(
                f"⚡ **GM Intervention:** **{castle_name}** seized and granted to {target.mention}."
            )

    @commands.command(name="toggleupkeep")
    @commands.has_permissions(administrator=True)
    async def toggle_upkeep(self, ctx: commands.Context):
        """Toggles daily army upkeep and attrition for the current game."""

        async with get_session() as session:
            # Find the active game for this guild
            result = await session.execute(
                select(Game).filter_by(guild_id=ctx.guild.id, is_active=True)
            )
            game = result.scalars().first()

            if not game:
                await ctx.send(
                    "❌ No active game found in this server. Please start a game first."
                )
                return

            # Flip the boolean value
            game.upkeep_enabled = not game.upkeep_enabled
            new_state = game.upkeep_enabled
            await session.commit()

            status_message = "ENABLED" if new_state else "DISABLED"
            await ctx.send(
                f"✅ **Daily upkeep is now `{status_message}` for the game '{game.name}'.**"
            )

    @commands.command(name="togglemanpower")
    @commands.has_permissions(administrator=True)
    async def toggle_manpower(self, ctx: commands.Context):
        """(GM Only) Enables or disables the entire recruitment system."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                await ctx.send("❌ No active game found in this server.")
                return

            # This part remains the same
            game.manpower_enabled = not game.manpower_enabled
            new_state = game.manpower_enabled
            await session.commit()

            # --- UPDATE THE FEEDBACK MESSAGE ---
            status_message = "ENABLED" if new_state else "DISABLED"
            await ctx.send(
                f"✅ **The recruitment system is now `{status_message}` for this game.**"
            )

    @commands.command(
        name="debug_terrain", description="(GM Only) Get terrain type at coordinates."
    )
    @commands.has_permissions(
        administrator=True
    )  # Restricts to server administrators for prefix commands
    async def debug_terrain(
        self, ctx: commands.Context, x: int, y: int
    ):  # <--- Changed to ctx
        """(GM Only) Get terrain type at specific X, Y coordinates."""

        # No need for defer if it's not a slash command, just send directly

        async with get_session() as session:
            service = WarfareService(session)
            terrain = service._debug_get_terrain_type(x, y)
            await ctx.send(  # <--- Changed to ctx.send
                f"Terrain at ({x},{y}): {terrain}"  # ephemeral not available for ctx.send
            )

    @commands.command(name="set_gm")
    @commands.has_permissions(administrator=True)
    async def set_gm(self, ctx, target: discord.Member, is_gm_status: bool):
        """
        Sets a user's Game Master status in the database.
        Usage: !set_gm @User True
        """
        async with get_session() as session:
            # Find or create the user record
            user = await session.scalar(
                select(User).where(User.discord_id == target.id)
            )

            if not user:
                user = User(discord_id=target.id)
                session.add(user)
                await ctx.send(f"✨ Created a new user record for {target.mention}.")

            # Set the flag
            user.is_gm = is_gm_status
            await session.commit()

            status_text = "a Game Master" if is_gm_status else "no longer a Game Master"
            await ctx.send(
                f"✅ **Success!** {target.mention} is now **{status_text}**."
            )


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
