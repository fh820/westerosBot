import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.db_manager import get_session
from app.db.repositories import GameRepo, HouseRepo
from app.services.gameplay_service import GameplayService
from app.db.models import House, GamePlayer, User, Army
from app.ui.paginator import Paginator
from app.checks import is_in_house_channel
import datetime
import re
from app.services.common import slugify


class GameplayCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pending_claim_users = set()

    async def _render_detailed_dashboard(
        self, ctx: commands.Context, data: dict, is_gm_view: bool = False
    ):
        """
        A centralized helper to render the detailed, paginated dashboard for a single house.
        UPDATED: Smaller chunk size (6) to prevent Discord 1024 char limit errors.
        """
        # 1. Ghost Army Filter
        raw_armies = data.get("armies", [])
        async with get_session() as session:
            if raw_armies:
                army_ids = [a["id"] for a in raw_armies]
                stmt_ghost = select(Army.army_id, Army.departure_time).where(
                    Army.army_id.in_(army_ids)
                )
                ghost_results = (await session.execute(stmt_ghost)).all()
                now = datetime.datetime.now(datetime.timezone.utc)
                ids_to_hide = set()
                for row in ghost_results:
                    a_id, dep_time = row
                    if dep_time:
                        dt_aware = (
                            dep_time.replace(tzinfo=datetime.timezone.utc)
                            if dep_time.tzinfo is None
                            else dep_time
                        )
                        if dt_aware > now:
                            ids_to_hide.add(a_id)
                data["armies"] = [a for a in raw_armies if a["id"] not in ids_to_hide]

        # 2. Sort Armies: Moving first, then by size
        # Status priority: MARCHING/SAILING -> DOCKED/GARRISONED -> IDLE
        def sort_key(a):
            moving = 0 if a["status"] in ["MARCHING", "SAILING"] else 1
            return (moving, -a["count"])  # Negative count for descending sort

        armies = sorted(data.get("armies", []), key=sort_key)

        # 3. Build Paginated Embeds
        color_val = int(data["color"].lstrip("#"), 16)

        # CRITICAL FIX: Reduced Chunk Size from 10 to 6
        # Discord Field Limit is 1024 chars. 10 armies overflow this.
        CHUNK_SIZE = 6
        army_chunks = (
            [armies[i : i + CHUNK_SIZE] for i in range(0, len(armies), CHUNK_SIZE)]
            if armies
            else [[]]
        )

        embeds = []
        title_prefix = "👑 GM Info:" if is_gm_view else "📜 Player Report:"
        total_pages = len(army_chunks)

        for i, chunk in enumerate(army_chunks):
            embed = discord.Embed(
                title=f"{title_prefix} House {data['house_name']} (ID: {data['house_id']})",
                color=discord.Color(color_val),
            )

            author_name = f"Head of House {data['house_name']}"
            if data["parent_house"]:
                author_name = f"Scion of House {data['parent_house']}"
            embed.set_author(name=author_name)

            # Footer for pages
            if total_pages > 1:
                embed.set_footer(text=f"Page {i+1} of {total_pages}")

            # --- Page 1 Only: Main Stats ---
            if i == 0:
                skills = data.get("skills", {})
                if skills:
                    stats_str = (
                        f"⚔️ **Mart:** {skills.get('martial', 0)} | "
                        f"📜 **Dip:** {skills.get('diplomacy', 0)} | "
                        f"💰 **Stew:** {skills.get('stewardship', 0)}\n"
                        f"👁️ **Int:** {skills.get('intrigue', 0)} | "
                        f"💪 **Prow:** {skills.get('prowess', 0)}"
                    )
                    embed.add_field(
                        name="Character Stats", value=stats_str, inline=False
                    )

                if data["is_primary_player_house"] or is_gm_view:
                    embed.add_field(
                        name="Treasury", value=f"{data['treasury']} Gold", inline=True
                    )
                    embed.add_field(
                        name="Income", value=f"+{data['income']} / year", inline=True
                    )
                    embed.add_field(
                        name="Manpower",
                        value=f"{data['manpower']} / {data['manpower_cap']}",
                        inline=True,
                    )

                if data["fiefs"]:
                    fief_str = ", ".join(data["fiefs"])
                    if len(fief_str) > 1000:
                        fief_str = fief_str[:1000] + "..."
                    embed.add_field(
                        name=f"Lands ({len(data['fiefs'])})",
                        value=fief_str,
                        inline=False,
                    )

                embed.add_field(
                    name=f"Total Military Strength: {data['total_troops']}",
                    value="­",
                    inline=False,
                )

            # --- All Pages: Army List ---
            if chunk:
                army_list_str = ""
                for army in chunk:
                    if army.get("status") == "EMBARKED":
                        continue

                    status_icons = {
                        "IDLE": "💤",
                        "GARRISONED": "🏰",
                        "DOCKED": "⚓",
                        "MARCHING": "🦶",
                        "SAILING": "⛵",
                    }
                    icon = status_icons.get(army["status"], "❓")

                    cargo_ind = " 📦" if army.get("cargo_count", 0) > 0 else ""
                    unit_noun = "Ships" if army.get("type") == "SEA" else "Troops"

                    # Compression for Composition to save space
                    comp_items = []
                    if army.get("comp"):
                        for k, v in army["comp"].items():
                            if v > 0:
                                comp_items.append(f"{k.title()[:3]}: {v}")
                    comp_str = " | ".join(comp_items) if comp_items else "-"

                    # Location string
                    loc_display = f"📍 {army['location']}"
                    if (
                        army["status"] in ["MARCHING", "SAILING"]
                        and army["destination"]
                    ):
                        loc_display += f" → {army['destination']}"

                    # Entry Construction
                    entry = (
                        f"**{icon} {army['name']} (ID: {army['id']})**\n"
                        f"{loc_display}\n"
                        f"**{unit_noun}:** {army['count']}{cargo_ind} | **Comp:** {comp_str}\n\n"
                    )

                    # Safety check: If adding this army exceeds limit, stop this page (rare with chunk 6)
                    if len(army_list_str) + len(entry) > 1020:
                        army_list_str += "*(...limit reached for this page)*"
                        break

                    army_list_str += entry

                if army_list_str:
                    embed.add_field(
                        name="Armies & Fleets", value=army_list_str, inline=False
                    )

            embeds.append(embed)

        if not embeds:
            await ctx.send("Could not generate the report.")
            return

        # Send with Paginator
        if len(embeds) > 1:
            view = Paginator(embeds)
            await ctx.send(embed=embeds[0], view=view)
        else:
            await ctx.send(embed=embeds[0])

    async def get_gm_channel(self, ctx):
        """Finds the #gm-requests channel, falling back to the current channel."""
        channel = discord.utils.get(ctx.guild.text_channels, name="gm-requests")
        return channel if channel else ctx.channel

    async def manage_house_role(self, ctx, house: House, member: discord.Member):
        """
        Ensures a Role exists for the house and assigns it to the specified member.
        Handles role creation, color setting, and permission errors gracefully.
        """
        role_name = house.name
        role = discord.utils.get(ctx.guild.roles, name=role_name)

        # 1. Create Role if it's missing
        if not role:
            try:
                color_val = int(house.color_hex.lstrip("#"), 16)
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

    async def create_private_channel(
        self, ctx, name: str, category_name: str, overwrites: dict
    ):
        """
        Generic helper to create a private text channel within a category.
        Checks if the channel already exists before creating a new one.
        """
        try:
            category = discord.utils.get(ctx.guild.categories, name=category_name)
            if not category:
                category = await ctx.guild.create_category(category_name)

            # Check if channel already exists to avoid duplicates
            existing_channel = discord.utils.get(ctx.guild.text_channels, name=name)
            if existing_channel:
                return existing_channel

            channel = await ctx.guild.create_text_channel(
                name=name, category=category, overwrites=overwrites
            )
            return channel
        except discord.Forbidden:
            await ctx.send(
                f"❌ **Permissions Error:** The bot lacks permissions to create channels or categories."
            )
            return None
        except Exception as e:
            await ctx.send(f"❌ An unexpected channel error occurred: {e}")
            return None

    @commands.command(name="claim")
    @commands.cooldown(
        1, 300, commands.BucketType.user
    )  # 1 use every 300 seconds (5 mins)
    async def request_claim(self, ctx, *, house_name: str):
        """
        Request to claim a house or character.
        Usage: !claim Stark  OR  !claim Sansa Stark
        """
        # 1. Channel Check
        if ctx.channel.name != "claims":
            claims_channel = discord.utils.get(ctx.guild.text_channels, name="claims")
            mention = claims_channel.mention if claims_channel else "#claims"
            await ctx.send(
                f"❌ You cannot claim here. Please use the {mention} channel."
            )
            ctx.command.reset_cooldown(ctx)  # Don't punish wrong channel
            return

        # 2. Pending Request Check
        if ctx.author.id in self.pending_claim_users:
            await ctx.send(
                f"❌ {ctx.author.mention}, you already have a pending claim request. Please wait for a GM to Approve or Deny it before submitting another."
            )
            return

        discord_id = ctx.author.id

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                await ctx.send("❌ No active game found. Ask a GM to `!setup`.")
                return

            service = GameplayService(session)

            valid, msg, parent_house = await service.validate_claim_request(
                game.game_id, discord_id, house_name
            )

            if not valid:
                await ctx.send(msg)
                ctx.command.reset_cooldown(
                    ctx
                )  # Reset cooldown on invalid syntax/target
                return

            # 3. Send Ticket
            gm_channel = await self.get_gm_channel(ctx)

            embed = discord.Embed(
                title="🔔 New Claim Request", color=discord.Color.gold()
            )
            embed.add_field(name="Player", value=ctx.author.mention, inline=True)

            # Display: "Sansa Stark (Child of Stark)" or just "Stark"
            display_name = (
                house_name if house_name != parent_house.name else parent_house.name
            )
            embed.add_field(name="Request", value=display_name, inline=True)

            embed.add_field(
                name="Approve Command",
                value=f"`!approve {ctx.author.id} {house_name}`",
                inline=False,
            )
            embed.add_field(
                name="Deny Command",
                value=f"`!deny {ctx.author.id} Reason...`",
                inline=False,
            )

            embed.set_footer(text="GMs: Copy the command above to approve.")

            await gm_channel.send(embed=embed)

            # 4. Lock the user
            self.pending_claim_users.add(ctx.author.id)

            await ctx.send(
                f"✅ **Request Sent.** The GM is reviewing your petition for **{house_name}**."
            )

    @request_claim.error
    async def request_claim_error(self, ctx, error):
        """Custom error handler for the claim command to show cooldowns."""
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"⏳ **Slow down!** You can only submit a claim request once every 5 minutes. Try again in {int(error.retry_after)} seconds.",
                delete_after=10,
            )
        else:
            raise error

    @commands.command(name="me", aliases=["info", "stats"])
    @commands.check(is_in_house_channel)
    async def player_info(self, ctx):
        """
        Displays your character stats, treasury, lands, and armies in your private channel.
        For GMs, displays a paginated list of all houses and their details.
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = GameplayService(session)
            data, error = await service.get_player_dashboard(
                game.game_id, ctx.author.id
            )

            if error:
                return await ctx.send(error)

            is_gm_response = isinstance(data, list)

            if is_gm_response:
                # --- GM DASHBOARD RENDERING ---
                # (Logic remains unchanged as per your request)
                gm_embeds = []
                sorted_data = sorted(data, key=lambda x: x["house_name"])
                for i, house_data in enumerate(sorted_data):
                    color_val = int(house_data["color"].lstrip("#"), 16)
                    embed = discord.Embed(
                        title=f"👑 GM Report: House {house_data['house_name']} (ID: {house_data['house_id']})",
                        color=discord.Color(color_val),
                    )
                    embed.add_field(
                        name="Commander", value=house_data["name"], inline=True
                    )
                    if house_data["parent_house"]:
                        embed.add_field(
                            name="Scion of",
                            value=house_data["parent_house"],
                            inline=True,
                        )
                    embed.add_field(
                        name="Treasury",
                        value=f"{house_data['treasury']} Gold",
                        inline=True,
                    )
                    embed.add_field(
                        name="Income",
                        value=f"+{house_data['income']} / year",
                        inline=True,
                    )
                    embed.add_field(
                        name="Manpower",
                        value=f"{house_data['manpower']} / {house_data['manpower_cap']}",
                        inline=True,
                    )

                    armies_summary = ""
                    total_idle_troops, total_moving_troops = 0, 0
                    for army in house_data.get("armies", []):
                        count = army["count"] + army.get("cargo_count", 0)
                        if army["status"] in ["IDLE", "GARRISONED", "DOCKED"]:
                            total_idle_troops += count
                        else:
                            total_moving_troops += count

                    if total_idle_troops > 0:
                        armies_summary += f"💤 Idle: {total_idle_troops} "
                    if total_moving_troops > 0:
                        armies_summary += f"🏃 Moving: {total_moving_troops} "
                    embed.add_field(
                        name="Forces",
                        value=armies_summary.strip() or "No forces.",
                        inline=False,
                    )
                    embed.set_footer(text=f"Page {i + 1} of {len(data)} (All Houses)")
                    gm_embeds.append(embed)

                view = Paginator(gm_embeds) if len(gm_embeds) > 1 else None
                await ctx.send(embed=gm_embeds[0], view=view)

            else:
                # --- REGULAR PLAYER DASHBOARD LOGIC (Updated for ID System) ---

                # 1. Fetch the GamePlayer record
                stmt_gp = (
                    select(GamePlayer)
                    .join(User)
                    .where(
                        User.discord_id == ctx.author.id,
                        GamePlayer.game_id == game.game_id,
                    )
                )
                gp = (await session.execute(stmt_gp)).scalars().first()

                # 2. Security & Auto-Lock Logic
                is_gm = ctx.author.guild_permissions.administrator
                allowed_channels = ["bot-testing", "gm-requests"]

                if not is_gm and ctx.channel.name not in allowed_channels:
                    # CASE A: User has a locked Channel ID in the DB
                    if gp and gp.private_channel_id:
                        if ctx.channel.id != gp.private_channel_id:
                            correct_chan = self.bot.get_channel(gp.private_channel_id)
                            mention = (
                                correct_chan.mention
                                if correct_chan
                                else "your private quarters"
                            )
                            return await ctx.send(
                                f"❌ **Security:** Please use this command in {mention} for privacy.",
                                delete_after=15,
                            )

                    # CASE B: Legacy Player (No ID locked yet). Use slug-check then AUTO-LOCK.
                    elif gp and not gp.private_channel_id:
                        slug_name = f"{slugify(data['house_name'])}-quarters"
                        char_slug = f"{slugify(data['name'])}-quarters"

                        if ctx.channel.name in [slug_name, char_slug]:
                            # User is in the right place! Lock the ID now.
                            gp.private_channel_id = ctx.channel.id
                            await session.commit()
                            await ctx.send(
                                "🔒 *System: Your private quarters have been linked to this channel.*",
                                delete_after=5,
                            )
                        else:
                            return await ctx.send(
                                "❌ **Security:** Please use this command in your private quarters.",
                                delete_after=15,
                            )

                # --- RENDERING DASHBOARD ---
                # (Logic remains unchanged, using 'data' from service)
                color_val = int(data["color"].lstrip("#"), 16)
                armies = data.get("armies", [])
                army_chunks = (
                    [armies[i : i + 6] for i in range(0, len(armies), 6)]
                    if armies
                    else [[]]
                )
                embeds = []

                for i, chunk in enumerate(army_chunks):
                    embed = discord.Embed(
                        title=f"📜 Player Report: {data['name']}",
                        color=discord.Color(color_val),
                    )
                    author_name = f"Head of House {data['house_name']}"
                    if data["parent_house"]:
                        author_name = f"Scion of House {data['parent_house']}"
                    embed.set_author(name=author_name)

                    if i == 0:
                        skills = data.get("skills", {})
                        if skills:
                            stats_str = (
                                f"⚔️ **Martial:** {skills.get('martial', 0)} | 📜 **Diplomacy:** {skills.get('diplomacy', 0)} | 💰 **Stewardship:** {skills.get('stewardship', 0)}\n"
                                f"👁️ **Intrigue:** {skills.get('intrigue', 0)} | 💪 **Prowess:** {skills.get('prowess', 0)}"
                            )
                            embed.add_field(
                                name="Character Stats", value=stats_str, inline=False
                            )
                        if data["is_primary_player_house"]:
                            embed.add_field(
                                name="Treasury",
                                value=f"{data['treasury']} Gold",
                                inline=True,
                            )
                            embed.add_field(
                                name="Income",
                                value=f"+{data['income']} / year",
                                inline=True,
                            )
                            embed.add_field(
                                name="Manpower",
                                value=f"{data['manpower']} / {data['manpower_cap']}",
                                inline=True,
                            )
                        if data["fiefs"]:
                            embed.add_field(
                                name=f"Lands ({len(data['fiefs'])})",
                                value=", ".join(data["fiefs"])[:1024],
                                inline=False,
                            )
                        embed.add_field(
                            name=f"Total Military Strength: {data['total_troops']}",
                            value="­",
                            inline=False,
                        )

                    if chunk:
                        army_list_str = ""
                        for army in chunk:
                            status_icons = {
                                "IDLE": "💤",
                                "GARRISONED": "🏰",
                                "DOCKED": "⚓",
                                "MARCHING": "🦶",
                                "SAILING": "⛵",
                            }
                            icon = status_icons.get(army["status"], "❓")
                            cargo_indicator = (
                                " 📦" if army.get("cargo_count", 0) > 0 else ""
                            )
                            unit_noun = (
                                "Ships" if army.get("type") == "SEA" else "Troops"
                            )

                            army_list_str += (
                                f"**{icon} {army['name']} (ID: {army['id']})**\n"
                            )
                            loc_display = f"📍 {army['location']}"
                            if (
                                army["status"] in ["MARCHING", "SAILING"]
                                and army["destination"]
                            ):
                                loc_display += f" → {army['destination']}"
                            army_list_str += f"{loc_display} | {unit_noun}: {army['count']}{cargo_indicator}\n\n"
                        embed.add_field(
                            name="Armies & Fleets", value=army_list_str, inline=False
                        )
                    embeds.append(embed)

                view = Paginator(embeds) if len(embeds) > 1 else None
                await ctx.send(embed=embeds[0], view=view)

    # --- GM COMMANDS ---

    @commands.command(name="approve")
    @commands.has_permissions(administrator=True)
    async def approve_claim(self, ctx, target: discord.Member, *, claim_string: str):
        """Approves a claim and locks the Channel ID in the DB."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = GameplayService(session)
            house_check = await HouseRepo.get_house_by_name(
                session, game.game_id, claim_string
            )
            is_character_claim = not bool(house_check)

            overwrites = {
                ctx.guild.default_role: discord.PermissionOverwrite(
                    read_messages=False
                ),
                target: discord.PermissionOverwrite(
                    read_messages=True, send_messages=True
                ),
                ctx.guild.me: discord.PermissionOverwrite(
                    read_messages=True, send_messages=True
                ),
            }

            target_house = None
            if is_character_claim:
                success, msg, faction, parent = await service.claim_character(
                    game.game_id, target.id, claim_string
                )
                target_house = faction
            else:
                success, msg, house_obj = await service.claim_house(
                    game.game_id, target.id, claim_string
                )
                target_house = house_obj

            if not success:
                return await ctx.send(msg)

            await self.manage_house_role(ctx, target_house, target)

            # --- CHANNEL CREATION & ID LOCKING ---
            slug = slugify(claim_string)
            c_name = f"{slug}-quarters"
            channel = await self.create_private_channel(
                ctx, c_name, "Great Houses", overwrites
            )

            if channel:
                stmt_gp = (
                    select(GamePlayer)
                    .join(User)
                    .where(
                        User.discord_id == target.id, GamePlayer.game_id == game.game_id
                    )
                )
                gp_record = (await session.execute(stmt_gp)).scalars().first()
                if gp_record:
                    gp_record.private_channel_id = channel.id
                    await session.commit()

                await channel.send(
                    f"Welcome to your quarters, {target.mention}. This channel is now linked to your ID."
                )

            await ctx.send(f"✅ Approved {target.mention} for **{claim_string}**.")

    @commands.command(name="deny")
    @commands.has_permissions(administrator=True)
    async def deny_claim(
        self,
        ctx,
        target: discord.Member,
        *,
        reason: str = "This claim has been denied by the Small Council.",
    ):
        """
        Denies a player's claim request and notifies them.
        Usage: !deny @User#1234 "Claim is unavailable."
        """
        try:
            embed = discord.Embed(
                title="❌ Petition Denied",
                description=f"Your recent claim has been **rejected**.",
                color=discord.Color.red(),
            )
            embed.add_field(name="Reason from the Council", value=reason, inline=False)
            await target.send(embed=embed)
            await ctx.send(
                f"✅ Denied claim for {target.mention} and notified them via DM."
            )
        except discord.Forbidden:
            await ctx.send(
                f"⚠️ Could not DM {target.mention}. They may have DMs disabled."
            )
        finally:
            self.pending_claim_users.discard(target.id)

    @commands.command(name="reset_claim_lock")
    @commands.has_permissions(administrator=True)
    async def reset_claim_lock(self, ctx, target: discord.Member):
        """
        GM Tool: Manually removes a user from the 'Pending Claim' lock list.
        Use this if a user gets stuck (e.g. if a GM deleted a ticket without using !deny).
        """
        if target.id in self.pending_claim_users:
            self.pending_claim_users.discard(target.id)
            await ctx.send(f"✅ Claim lock reset for {target.mention}.")
        else:
            await ctx.send(f"ℹ️ {target.mention} was not locked.")

    @commands.command(name="gm_info", aliases=["gm_dashboard"])
    @commands.has_permissions(administrator=True)
    async def gm_info(self, ctx, *, house_name: str):
        """
        GM Tool: Displays a detailed dashboard for a specific house by name.
        Usage: !gm_info "House Ball"
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = GameplayService(session)
            data, error = await service.get_house_dashboard(game.game_id, house_name)

            if error:
                return await ctx.send(error)

            await self._render_detailed_dashboard(ctx, data, is_gm_view=True)


async def setup(bot):
    await bot.add_cog(GameplayCog(bot))
