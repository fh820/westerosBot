import discord
from discord.ext import commands
from sqlalchemy import select, update, delete
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
from app.db.repositories import GameRepo
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from app.services.warfare_service import WarfareService
from app.services.common import slugify
import os
import json


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

    async def manage_house_role(self, ctx, house, member: discord.Member):
        """
        Ensures a Role exists for the house and assigns it to the specified member.
        Handles role creation, color setting, and permission errors gracefully.
        """
        role_name = house.name
        role = discord.utils.get(ctx.guild.roles, name=role_name)

        # 1. Create Role if it's missing
        if not role:
            try:
                # Default to grey if color_hex is missing
                hex_str = house.color_hex or "#808080"
                color_val = int(hex_str.lstrip("#"), 16)

                role = await ctx.guild.create_role(
                    name=role_name, color=discord.Color(color_val), mentionable=True
                )
                await ctx.send(f"✨ Created new role: **{role.name}**")
            except Exception as e:
                await ctx.send(f"⚠️ Failed to create Role for {role_name}: {e}")
                return None

        # 2. Assign Role to the Member
        try:
            if role not in member.roles:
                await member.add_roles(role)
                await ctx.send(f"🎖️ Assigned role **{role.name}** to {member.mention}")
        except discord.Forbidden:
            await ctx.send(
                f"❌ **Permissions Error:** The bot's role is below the **{role.name}** role. Cannot assign."
            )
        except Exception as e:
            await ctx.send(
                f"❌ An unexpected error occurred while assigning the role: {e}"
            )

        return role

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

    def apply_world_patch(self, base_data: list, patch_data: list) -> tuple[list, list]:
        """
        Overwrites base_data stats with patch_data values matching by 'castle'.
        Updates ALL fields present in the patch (Region, Liege, Coords, Stats, etc).
        Returns: (Patched Data List, List of Strings describing changes)
        """
        # Convert base_data to a dict for fast lookup: { "Winterfell": {data...}, ... }
        world_map = {entry["castle"]: entry for entry in base_data}
        logs = []

        # List of simple fields that can be directly overwritten
        direct_fields = [
            "region",
            "house",
            "liege",
            "x",
            "y",
            "is_ruined",
            "base_income",
            "house_type",
            "ancestral_weapon",
        ]

        for patch in patch_data:
            castle_name = patch.get("castle")

            # Skip if castle doesn't exist in master data
            if not castle_name or castle_name not in world_map:
                logs.append(f"⚠️ Skipped '{castle_name}': Not found in master data.")
                continue

            target = world_map[castle_name]
            changes = []

            # 1. Update Direct Fields (Loop through the list above)
            for field in direct_fields:
                if field in patch:
                    old_val = target.get(field)
                    new_val = patch[field]

                    # Only apply and log if the value is actually different
                    if old_val != new_val:
                        target[field] = new_val
                        # Formatting for log readability
                        if field == "base_income":
                            changes.append(f"💰 Income: {new_val}")
                        elif field == "is_ruined":
                            changes.append(f"🔥 Ruined: {new_val}")
                        elif field == "liege":
                            changes.append(f"👑 Liege: {new_val}")
                        else:
                            changes.append(f"{field}: {new_val}")

            # 2. Update Army Stats (Nested Merge)
            # We merge keys, so you can update 'ships' without deleting 'infantry'
            if "army_stats" in patch:
                if "army_stats" not in target:
                    target["army_stats"] = {}

                for unit, count in patch["army_stats"].items():
                    old_count = target["army_stats"].get(unit, 0)
                    if old_count != count:
                        target["army_stats"][unit] = count
                        changes.append(f"⚔️ {unit.title()}: {count}")

            if changes:
                logs.append(f"**{castle_name}**: " + ", ".join(changes))

        # Return the list values back
        return list(world_map.values()), logs

    @commands.command(name="setup_game")
    @commands.has_permissions(administrator=True)
    async def setup_game(
        self, ctx, ruling_house: str = "Targaryen", mode: str = "SPLIT"
    ):
        """
        Usage: !setup_game Baratheon SPLIT
        Optional: Attach a 'patch.json' to change specific stats.
        """
        mode = mode.upper()
        if mode not in ["SPLIT", "UNIFIED"]:
            return await ctx.send("❌ Invalid Mode.")

        # 1. Check Active Game
        async with get_session() as session:
            stmt = select(Game).where(
                Game.guild_id == ctx.guild.id, Game.is_active == True
            )
            if (await session.execute(stmt)).scalars().first():
                return await ctx.send("⚠️ Game Active. End it first.")

        # 2. Load Master Data (Always required as base)
        if not os.path.exists("master_world_data.json"):
            return await ctx.send(
                "❌ Critical: 'master_world_data.json' missing on server."
            )

        with open("master_world_data.json", "r", encoding="utf-8") as f:
            world_data = json.load(f)

        status_msg = f"🌍 **Initializing World...**\n👑 Crown: **{ruling_house}**\n⚙️ Mode: **{mode}**"

        # 3. Handle Patch File (If attached)
        patch_log = ""
        if ctx.message.attachments:
            try:
                att = ctx.message.attachments[0]
                if not att.filename.endswith(".json"):
                    return await ctx.send("❌ Patch must be .json")

                patch_bytes = await att.read()
                patch_data = json.loads(patch_bytes.decode("utf-8"))

                # Apply the patch logic
                world_data, logs = self.apply_world_patch(world_data, patch_data)

                status_msg += f"\n📥 **Patch Applied:** {len(logs)} modifications."
                if logs:
                    # Create a snippet of logs (don't spam if too long)
                    preview = "\n".join(logs[:5])
                    if len(logs) > 5:
                        preview += f"\n...and {len(logs)-5} more."
                    patch_log = f"\n```{preview}```"

            except json.JSONDecodeError:
                return await ctx.send("❌ Patch JSON is invalid.")
            except Exception as e:
                return await ctx.send(f"❌ Patch Error: {e}")

        msg = await ctx.send(status_msg + patch_log)

        # 4. Initialize World
        async with get_session() as session:
            setup = SetupService(session)

            # Use the init_world method from previous answer (that accepts a list)
            success, message = await setup.init_world(
                guild_id=ctx.guild.id,
                gm_discord_id=ctx.author.id,
                world_data=world_data,  # <--- The Patched Data
                ruling_house_name=ruling_house,
                era_mode=mode,
            )

            if success:
                await msg.edit(content=f"{message}\n🔨 **Constructing Channels...**")
                try:
                    count = await self.create_logistics_channels(ctx)
                    await ctx.send(f"✅ **Ready.** Created {count} channels.")
                except AttributeError:
                    # Fallback if create_logistics_channels isn't in this class
                    await ctx.send(
                        "✅ **Ready.** World created (Channel setup skipped)."
                    )
                except Exception as e:
                    await ctx.send(
                        f"✅ **Ready.** World created, but channel error: {e}"
                    )
                await msg.edit(content=f"{message}\n✅ **Setup Complete.**")
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
    async def set_head(self, ctx, target: discord.Member, *, house_name: str):
        """
        Assigns a player as the head of a house.
        Correctly handles multi-word house names.
        Usage: !set_head @Player Targaryen of King's Landing
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            # Now, `house_name` will be the full string "Targaryen of King's Landing"
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
                .options(selectinload(GamePlayer.character))
            )
            player = (await session.execute(stmt_p)).scalars().first()

            if not player:
                await ctx.send(
                    f"❌ {target.display_name} must `!claim` a character first."
                )
                return

            # Demote old head
            stmt_old = select(GamePlayer).where(
                GamePlayer.claimed_house_id == house.house_id,
                GamePlayer.is_primary == True,
            )
            old_head = (await session.execute(stmt_old)).scalars().first()
            if old_head:
                old_head.is_primary = False

            # Update Player
            player.claimed_house_id = house.house_id
            player.is_primary = True

            # Sync Character to the new house
            if player.character:
                player.character.house_id = house.house_id
                player.character.is_head = True

            await session.commit()
            await ctx.send(
                f"👑 **Succession:** {target.mention} is now the **Head of House {house.name}**."
            )

    @commands.command(name="vacate")
    @commands.has_permissions(administrator=True)
    async def vacate(self, ctx, target: discord.Member):
        """Removes a claim and deletes the channel using the LOCKED ID."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            stmt = (
                select(GamePlayer)
                .join(User)
                .where(GamePlayer.game_id == game.game_id, User.discord_id == target.id)
                .options(
                    selectinload(GamePlayer.house).selectinload(House.dynasty),
                    selectinload(GamePlayer.character),
                )
            )
            player_claim = (await session.execute(stmt)).scalars().first()
            if not player_claim:
                return await ctx.send(f"❌ No active claim for {target.mention}.")

            claimed_house = player_claim.house
            claimed_char = player_claim.character
            locked_channel_id = player_claim.private_channel_id

            # 1. CHANNEL CLEANUP
            deleted_successfully = False
            if locked_channel_id:
                channel = self.bot.get_channel(locked_channel_id)
                if channel:
                    await channel.delete(reason="Claim vacated")
                    await ctx.send(
                        f"🧹 Deleted quarters channel (ID: {locked_channel_id})"
                    )
                    deleted_successfully = True

            if not deleted_successfully:
                # Fallback to slug search for legacy claims
                slug_name = slugify(
                    claimed_char.name if claimed_char else claimed_house.name
                )
                if fb_chan := discord.utils.get(
                    ctx.guild.text_channels, name=f"{slug_name}-quarters"
                ):
                    await fb_chan.delete()
                    await ctx.send(
                        f"🧹 Fallback: Deleted channel by name `#{fb_chan.name}`"
                    )

            # 2. ROLE CLEANUP
            roles_to_remove = []
            role_names = [claimed_house.name, f"House {claimed_house.name}"]
            if claimed_char:
                role_names.append(claimed_char.name)

            for r_name in role_names:
                if r := discord.utils.get(ctx.guild.roles, name=r_name):
                    roles_to_remove.append(r)

            # System roles
            sys_roles = [
                "SmallCouncil",
                "Hand of the King",
                "Master of Coin",
                "Lord Commander",
            ]
            for r_name in sys_roles:
                if (
                    r := discord.utils.get(ctx.guild.roles, name=r_name)
                ) and r in target.roles:
                    roles_to_remove.append(r)

            if roles_to_remove:
                await target.remove_roles(*list(set(roles_to_remove)))
                await ctx.send("🎖️ Roles cleaned.")

            # 3. DATABASE DELETE
            await session.delete(player_claim)
            await session.commit()
            await ctx.send(f"✅ Vacated claim for {target.mention}.")

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

    @commands.command(name="set_crown")
    @commands.has_permissions(administrator=True)
    async def set_crown(self, ctx, target: discord.Member):
        """
        Assigns a user to the main Royal House (King's Landing),
        preserves their channel, and fixes their old house's vassalage.
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Find the Royal House (King's Landing)
            stmt_royal = (
                select(House)
                .join(Fief)
                .where(Fief.name == "King's Landing", House.game_id == game.game_id)
            )
            royal_house = (await session.execute(stmt_royal)).scalars().first()

            if not royal_house:
                return await ctx.send("❌ Royal House (King's Landing) not found.")

            # 2. Find the Player's Current State
            stmt_player = (
                select(GamePlayer)
                .join(User)
                .where(User.discord_id == target.id, GamePlayer.game_id == game.game_id)
                .options(selectinload(GamePlayer.house))  # Load old house data
            )
            player_record = (await session.execute(stmt_player)).scalars().first()

            if not player_record:
                return await ctx.send(f"❌ {target.mention} is not in the game yet.")

            # Capture old house info before switching
            old_house = player_record.house
            current_channel_id = player_record.private_channel_id

            # 3. The Switch
            # Move player to Royal House
            player_record.claimed_house_id = royal_house.house_id

            # CRITICAL: Ensure the private channel stays linked to this player
            # (If they had a channel, keep it. If not, it stays None)
            player_record.private_channel_id = current_channel_id

            # 4. Fix the "Infinite Loop" / Vassalage
            # The house they left (e.g., Dragonstone) must now submit to the King
            if old_house and old_house.house_id != royal_house.house_id:
                old_house.liege_id = royal_house.house_id

                # Check for self-referential loop on the Royal House just in case
                if royal_house.liege_id == royal_house.house_id:
                    royal_house.liege_id = None

                msg_extra = (
                    f"House {old_house.name} is now a vassal of {royal_house.name}."
                )
            else:
                msg_extra = ""

            await session.commit()

            # 5. Discord Side: Rename the channel and Update Roles
            if current_channel_id:
                channel = ctx.guild.get_channel(current_channel_id)
                if channel:
                    try:
                        # Rename channel to reflect new status
                        await channel.edit(name=f"royal-quarters-{target.display_name}")
                        await channel.send(
                            f"👑 **All Hail His Grace!** This channel is now the seat of **{royal_house.name}**."
                        )
                    except discord.Forbidden:
                        pass  # Bot might not have permission to rename, ignore

            # Assign new Role
            await self.manage_house_role(ctx, royal_house, target)

            await ctx.send(
                f"👑 **The Iron Throne:** {target.mention} is now the **King** ({royal_house.name}).\n"
                f"📝 **Log:** Player moved. {msg_extra}"
            )

    @commands.command(name="set_heir")
    @commands.has_permissions(administrator=True)
    async def set_heir(self, ctx, target: discord.Member):
        """Assigns a user to the Heir House (Dragonstone)."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = (
                select(House)
                .join(Fief)
                .where(Fief.name == "Dragonstone", House.game_id == game.game_id)
            )
            house = (await session.execute(stmt)).scalars().first()
            if not house:
                return await ctx.send("❌ Heir House not found.")

            await self._assign_player_to_house(session, game.game_id, target, house)
            await ctx.send(
                f"🐉 **Dragonstone:** {target.mention} is now the **Crown Prince** ({house.name})."
            )

    async def _assign_player_to_house(self, session, game_id, target, house):
        # 1. Get or create User
        stmt_u = select(User).where(User.discord_id == target.id)
        user = (await session.execute(stmt_u)).scalars().first()
        if not user:
            user = User(discord_id=target.id)
            session.add(user)
            await session.flush()

        # 2. Get or create GamePlayer record
        # ADDED: selectinload(GamePlayer.character) so we can update the character too
        stmt_p = (
            select(GamePlayer)
            .where(GamePlayer.user_id == user.user_id, GamePlayer.game_id == game_id)
            .options(selectinload(GamePlayer.character))
        )
        player = (await session.execute(stmt_p)).scalars().first()

        if not player:
            player = GamePlayer(game_id=game_id, user_id=user.user_id)
            session.add(player)
            await session.flush()

        # 3. Update Status
        player.claimed_house_id = house.house_id
        player.private_channel_id = None
        player.is_primary = True

        # NEW: Ensure the character follows the player to the new house
        if player.character:
            player.character.house_id = house.house_id
            player.character.is_head = True

        await session.commit()

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def load_scenario(self, ctx, scenario_name: str):
        """Applies a historical patch."""
        async with get_session() as session:
            service = ScenarioService(session)
            await ctx.send(f"⏳ Loading scenario `{scenario_name}`...")
            success, msg = await service.load_scenario(ctx.guild.id, scenario_name)
            await ctx.send(msg)

    @commands.command(name="force_grant")
    @commands.has_permissions(administrator=True)
    async def force_grant(self, ctx, target: discord.Member, *, castle_name: str):
        """
        GM Tool: Force transfer a fief, its garrisons, its vassals, AND treasury to a player.
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game found.")

            # 1. Find Fief (Slugified search for robustness)
            stmt_fief = select(Fief).where(Fief.game_id == game.game_id)
            all_fiefs = (await session.execute(stmt_fief)).scalars().all()
            fief = next(
                (f for f in all_fiefs if slugify(f.name) == slugify(castle_name)), None
            )

            if not fief:
                return await ctx.send(f"❌ Fief **{castle_name}** not found.")

            # 2. Find the Target Player
            stmt_target = (
                select(GamePlayer)
                .join(User)
                .where(User.discord_id == target.id, GamePlayer.game_id == game.game_id)
            )
            target_p = (await session.execute(stmt_target)).scalars().first()

            if not target_p or not target_p.claimed_house_id:
                return await ctx.send(f"❌ {target.display_name} has no active claim.")

            old_owner_house_id = fief.owner_id
            new_house_id = target_p.claimed_house_id

            if old_owner_house_id == new_house_id:
                return await ctx.send("❌ This house already owns this fief.")

            # 3. Transfer Fief Ownership
            fief.owner_id = new_house_id

            # 4. Transfer Armies (Land & Sea at that location)
            stmt_army = select(Army).where(
                Army.game_id == game.game_id,
                Army.location_x == fief.location_x,
                Army.location_y == fief.location_y,
            )
            armies_at_loc = (await session.execute(stmt_army)).scalars().all()
            for army in armies_at_loc:
                army.house_id = new_house_id

            # 5. Transfer Vassalage (Houses sworn to the old owner become sworn to new)
            stmt_vassals = select(House).where(
                House.liege_id == old_owner_house_id, House.game_id == game.game_id
            )
            vassals = (await session.execute(stmt_vassals)).scalars().all()
            for vassal in vassals:
                vassal.liege_id = new_house_id

            # 5.5. Transfer Treasury (NEW LOGIC)
            transferred_gold = 0
            if old_owner_house_id:
                # Fetch both houses to handle gold
                old_house = await session.get(House, old_owner_house_id)
                new_house = await session.get(House, new_house_id)

                if old_house and new_house:
                    transferred_gold = old_house.treasury
                    if transferred_gold > 0:
                        new_house.treasury += transferred_gold
                        old_house.treasury = 0

            # 6. Commit and Recalculate
            await session.commit()

            # Recalculate manpower since fief/vassals changed
            setup_service = SetupService(session)
            await setup_service.calculate_initial_manpower(game.game_id)
            await session.commit()

            # 7. Notification
            msg_header = (
                f"⚡ **GM Intervention:** **{fief.name}** granted to {target.mention}."
            )

            if target_p.private_channel_id:
                chan = self.bot.get_channel(target_p.private_channel_id)
                if chan:
                    await chan.send(
                        f"🏰 **Proclamation:** You have been granted the lordship of **{fief.name}**.\n"
                        f"💰 **Treasury Seized:** {transferred_gold} Gold."
                    )

            await ctx.send(
                f"{msg_header}\n"
                f"🎖️ **{len(armies_at_loc)}** armies transferred.\n"
                f"🛡️ **{len(vassals)}** vassals transferred.\n"
                f"💰 **{transferred_gold}** gold transferred."
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
