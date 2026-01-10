import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import re
import datetime
from typing import List, Literal
from app.db.db_manager import get_session
from app.db.models import User, GamePlayer, House, Character, PendingBannerCall
from app.db.repositories import GameRepo
from app.services.diplomacy_service import DiplomacyService
from app.ui.paginator import Paginator
from app.ui.social_views import ProposalView
from app.checks import is_in_house_channel
from app.ui.banner_view import BannerControlView
from app.services.common import slugify


async def is_gm(ctx):
    async with get_session() as session:
        # Assuming User model has an `is_gm` boolean field
        user = await session.scalar(
            select(User).where(User.discord_id == ctx.author.id)
        )
        if user and user.is_gm:
            return True
        return False


class DiplomacyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _handle_union_proposal(
        self, ctx: commands.Context, query: str, action_name: str, icon: str
    ):
        """A shared helper to process unions using the Locked Channel ID system."""
        parts = re.split(r"\s+to\s+", query, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2:
            return await ctx.send(
                f'❌ Format: `!{ctx.invoked_with} "[Person A]" to "[Person B]"`'
            )

        char_a_name, char_b_name = parts[0].strip("\"' "), parts[1].strip("\"' ")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = DiplomacyService(session)

            # 1. IDENTIFY ARRANGER
            arranger_player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )
            if not arranger_player:
                return await ctx.send("❌ You are not an active player.")

            # 2. IDENTIFY CHARACTERS
            char_a = await service.find_or_create_char(game.game_id, char_a_name)
            char_b = await service.find_or_create_char(game.game_id, char_b_name)
            if not char_a or not char_b:
                return await ctx.send("❌ One or more characters could not be found.")

            if char_a.spouse_id or char_b.spouse_id:
                return await ctx.send("❌ One of these characters is already married.")

            # 3. CHECK AUTHORITY
            if not await service.check_marriage_authority(arranger_player, char_a):
                return await ctx.send(
                    f"❌ You do not have authority over **{char_a.name}**."
                )

            # 4. FIND CONSENTER (The player who owns Char B)
            # Ensure the service method returns the GamePlayer object including private_channel_id
            consenting_player_obj = await service.find_consenting_player(char_b)

            async def on_accept(interaction: discord.Interaction):
                success, msg = await service.execute_marriage(
                    game.game_id, char_a.name, char_b.name
                )
                # Marriage channel is still global/public
                news_channel = discord.utils.get(
                    interaction.guild.text_channels, name="marriages"
                )
                if success and news_channel:
                    await news_channel.send(f"{icon} {msg}")

                final_embed = interaction.message.embeds[0]
                final_embed.set_footer(text=f"✅ {action_name} Confirmed!")
                final_embed.color = discord.Color.green()
                await interaction.edit_original_response(embed=final_embed, view=None)

            # --- BRANCH A: NPC (GM Approval Needed) ---
            if not consenting_player_obj:
                gm_channel = discord.utils.get(
                    ctx.guild.text_channels, name="gm-alerts"
                )
                if not gm_channel:
                    return await ctx.send("❌ GM alerts channel not found.")

                proposal_embed = discord.Embed(
                    title=f"{icon} GM Approval: {action_name}",
                    description=f"**{ctx.author.display_name}** requests a union between **{char_a.name}** and the NPC **{char_b.name}**.",
                    color=discord.Color.dark_purple(),
                )
                view = ProposalView(
                    initiator=ctx.author,
                    consenter=ctx.author,
                    action_name=action_name,
                    proposal_embed=proposal_embed,
                    on_accept_callback=on_accept,
                    is_gm_approval=True,
                )
                await gm_channel.send(embed=proposal_embed, view=view)
                return await ctx.send("✅ Proposal sent to the GMs for approval.")

            # --- BRANCH B: SELF-ARRANGED (Auto-Accept) ---
            elif consenting_player_obj.user.discord_id == ctx.author.id:
                success, msg = await service.execute_marriage(
                    game.game_id, char_a.name, char_b.name
                )
                news_channel = discord.utils.get(
                    ctx.guild.text_channels, name="marriages"
                )
                if success and news_channel:
                    await news_channel.send(f"{icon} {msg}")
                return await ctx.send(msg)

            # --- BRANCH C: PLAYER CONSENT (ID-BASED QUARTERS) ---
            else:
                consenter_id = consenting_player_obj.user.discord_id
                try:
                    consenter_member = ctx.guild.get_member(
                        consenter_id
                    ) or await ctx.guild.fetch_member(consenter_id)
                except discord.NotFound:
                    return await ctx.send(
                        "❌ The recipient is no longer in the server."
                    )

                proposal_embed = discord.Embed(
                    title=f"{icon} {action_name} Proposal",
                    description=f"**{ctx.author.display_name}** proposes a union between **{char_a.name}** and **{char_b.name}**.",
                    color=discord.Color.purple(),
                )

                # FIND LOCKED CHANNEL ID
                target_channel = None
                if consenting_player_obj.private_channel_id:
                    target_channel = self.bot.get_channel(
                        consenting_player_obj.private_channel_id
                    )

                view = ProposalView(
                    initiator=ctx.author,
                    consenter=consenter_member,
                    action_name=action_name,
                    proposal_embed=proposal_embed,
                    on_accept_callback=on_accept,
                )

                if target_channel:
                    # Deliver to Private Quarters
                    await target_channel.send(
                        content=f"{consenter_member.mention}, a new proposal has arrived for your consideration.",
                        embed=proposal_embed,
                        view=view,
                    )
                    await ctx.send(
                        f"📬 **Proposal Dispatched:** A raven has been sent to the private quarters of **{consenter_member.display_name}**."
                    )
                else:
                    # Fallback to current channel if ID is missing or channel was deleted
                    await ctx.send(
                        content=f"{consenter_member.mention}, a proposal awaits your decision.",
                        embed=proposal_embed,
                        view=view,
                    )

    async def _send_gm_levy_alert(
        self,
        *,
        ctx: commands.Context,
        liege_house: House,  # Now directly take House object
        rally_point: str,
        results: List[str],
        player_vassals: List[dict],
        levy_type: Literal["Land", "Naval"],
    ):
        """
        A robust, reusable helper to send detailed alerts to the #gm-alerts channel.
        """
        channel = discord.utils.get(ctx.guild.text_channels, name="gm-alerts")

        if not channel:
            print(
                f"GM Alert failed: Could not find channel named 'gm-alerts' in guild {ctx.guild.id}"
            )
            return

        success_count = sum(1 for r in results if r.startswith("✅"))
        fail_count = sum(1 for r in results if r.startswith("⚠️"))
        no_units_count = sum(1 for r in results if r.startswith(("🍂", "💨")))

        title = (
            "GM Alert: Banners Called"
            if levy_type == "Land"
            else "GM Alert: Naval Levies Called"
        )
        color = (
            discord.Color.gold() if levy_type == "Land" else discord.Color.dark_blue()
        )

        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.description = f"**{liege_house.name}** has summoned their {levy_type.lower()} vassals to **{rally_point}**."

        # If this was a GM-initiated call for an NPC, we indicate the GM initiated it
        if ctx.author.guild_permissions.administrator:  # Simplified check for admin/GM
            embed.add_field(
                name="Initiated By GM",
                value=f"{ctx.author.mention} (`{ctx.author.display_name}`)",
                inline=False,
            )
        else:  # Regular player call
            embed.add_field(
                name="Liege Lord",
                value=f"{ctx.author.mention} (`{ctx.author.display_name}`)",
                inline=False,
            )

        unit_type = "Levies" if levy_type == "Land" else "Fleets"
        summary_value = (
            f"Player Vassals Notified: **{len(player_vassals)}**\n"
            f"NPC {unit_type} Responding: **{success_count}**\n"
            f"NPCs Unable to Muster: **{fail_count}**\n"
            f"NPCs with No Units: **{no_units_count}**"
        )
        embed.add_field(name="Muster Summary", value=summary_value, inline=False)
        embed.add_field(
            name="Context",
            value=f"[Jump to Command]({ctx.message.jump_url})",
            inline=False,
        )
        embed.set_footer(text=f"Liege House ID: {liege_house.house_id}")

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            print(f"GM Alert failed: Bot lacks permissions in #{channel.name}")
        except Exception as e:
            print(f"An unexpected error occurred sending GM alert: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """This is crucial for making the views persistent."""
        print("Checking for pending banner calls...")
        async with get_session() as session:
            stmt = select(PendingBannerCall).where(
                PendingBannerCall.status == "PENDING_APPROVAL"
            )
            pending_calls = (await session.execute(stmt)).scalars().all()
            for call in pending_calls:
                self.bot.add_view(
                    BannerControlView(call.id), message_id=call.gm_message_id
                )
            print(f"Re-initialized {len(pending_calls)} pending banner control panels.")

    @commands.command(name="call_banners")
    @commands.check(is_in_house_channel)
    async def call_banners(self, ctx, *, rally_point: str):
        """Initiates a banner call. Uses Locked Channel IDs to notify player vassals."""
        player_wait_msg = await ctx.send(
            f"🦅 **Verifying rally point '{rally_point}'...**"
        )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await player_wait_msg.edit(content="❌ No active game.")

            # --- 1. VALIDATE LOCATION ---
            from app.services.warfare_service import WarfareService

            war_service = WarfareService(session)
            rally_coords = await war_service._get_location_from_db(
                game.game_id, rally_point
            )

            if not rally_coords:
                return await player_wait_msg.edit(
                    content=f"❌ **Invalid Location:** '{rally_point}' is not recognized."
                )

            # --- 2. GET PLAYER & HOUSE ---
            stmt_l = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
                .options(selectinload(GamePlayer.house))
            )
            liege_p = (await session.execute(stmt_l)).scalars().first()

            if not liege_p or not liege_p.claimed_house_id:
                return await player_wait_msg.edit(
                    content="❌ You do not command a house."
                )

            # --- 3. SPAM CHECK ---
            stmt_check = select(PendingBannerCall).where(
                PendingBannerCall.game_id == game.game_id,
                PendingBannerCall.liege_house_id == liege_p.claimed_house_id,
                PendingBannerCall.status == "PENDING_APPROVAL",
            )
            if (await session.execute(stmt_check)).scalars().first():
                return await player_wait_msg.edit(
                    content=f"❌ **Hold:** You already have a banner call pending approval."
                )

            await player_wait_msg.edit(content="🦅 **Preparing banner call...**")

            # --- 4. SERVICE CALL (Recursive logic) ---
            service = DiplomacyService(session)
            success, npc_data, player_vassals = await service.prepare_banner_call(
                game.game_id,
                liege_discord_id=ctx.author.id,
            )

            if not success:
                return await player_wait_msg.edit(content=f"❌ {npc_data}")

            if not npc_data and not player_vassals:
                return await player_wait_msg.edit(
                    content="❌ You have no vassals to call."
                )

            # --- 5. NOTIFY PLAYER VASSALS (LOCKED ID SYSTEM) ---
            sent_count = 0
            liege_name = liege_p.house.name

            if player_vassals:
                for pv in player_vassals:
                    # 'pv' now contains: house_name, character_name, user_id, private_channel_id
                    target_chan_id = pv.get("private_channel_id")
                    vassal_user_id = pv.get("user_id")

                    vassal_channel = (
                        self.bot.get_channel(target_chan_id) if target_chan_id else None
                    )

                    embed = discord.Embed(
                        title="🦅 A Call to Arms!",
                        description=f"**House {liege_name}** has called the banners!\n\n"
                        f"My Lord of **{pv['house_name']}**, your liege summons your forces to rally at **{rally_point}**.",
                        color=discord.Color.dark_red(),
                    )
                    embed.add_field(
                        name="Instructions",
                        value=f"Muster your troops and use `!march` to proceed to **{rally_point}**.",
                    )
                    embed.set_thumbnail(url="https://img.icons8.com/color/96/war.png")

                    try:
                        if vassal_channel:
                            # Deliver to the Locked Channel ID
                            await vassal_channel.send(
                                f"<@{vassal_user_id}>", embed=embed
                            )
                            sent_count += 1
                        else:
                            # Fallback: Find the member and DM them if channel ID is missing/channel deleted
                            member = ctx.guild.get_member(
                                vassal_user_id
                            ) or await ctx.guild.fetch_member(vassal_user_id)
                            if member:
                                await member.send(
                                    f"⚠️ **Banner Call Notice:** (House channel not linked)",
                                    embed=embed,
                                )
                                sent_count += 1
                    except Exception as e:
                        print(
                            f"[ERROR] Failed to notify vassal {pv['house_name']}: {e}"
                        )
                        continue

            # --- 6. HANDLE NPC VASSALS (GM Panel) ---
            gm_msg_part = ""
            if npc_data:
                gm_channel = discord.utils.get(
                    ctx.guild.text_channels, name="gm-alerts"
                )
                if not gm_channel:
                    return await player_wait_msg.edit(
                        content="❌ #gm-alerts channel missing."
                    )

                vassal_data_for_db = [
                    {
                        "house_id": v["house_id"],
                        "house_name": v["house_name"],
                        "max_troops": v["max_amount"],
                        "percent": v["percent"],
                        "home_x": v.get("home_x", 0),
                        "home_y": v.get("home_y", 0),
                        "breakdown": v.get("breakdown", ""),
                    }
                    for v in npc_data
                ]

                new_pending_call = PendingBannerCall(
                    game_id=game.game_id,
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    message_id=player_wait_msg.id,
                    gm_channel_id=gm_channel.id,
                    gm_message_id=0,
                    liege_house_id=liege_p.house.house_id,
                    rally_point_name=rally_point,
                    vassal_data=vassal_data_for_db,
                    call_type="LAND",
                )
                session.add(new_pending_call)
                await session.flush()

                view = BannerControlView(new_pending_call.id)
                embed = await view.create_embed(pending_call=new_pending_call)
                gm_panel_msg = await gm_channel.send(embed=embed, view=view)

                new_pending_call.gm_message_id = gm_panel_msg.id
                await session.commit()
                gm_msg_part = " NPC levies have been requested from the GMs."

            await player_wait_msg.edit(
                content=f"✅ **Call Sent!** Ravens dispatched to {sent_count} player vassals.{gm_msg_part} Rally Point: **{rally_point}**."
            )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """
        Handles interactions for Banner Call buttons (Confirm/Cancel).
        """
        custom_id = interaction.data.get("custom_id")
        if not custom_id or not custom_id.startswith("banner_"):
            return

        # Acknowledge the interaction immediately so the button stops spinning
        # We use defer() so we have 15 minutes to reply/edit
        await interaction.response.defer()

        # Parse ID: "banner_confirm_123" -> action="confirm", call_id=123
        try:
            parts = custom_id.split("_")
            action = parts[1]
            call_id = int(parts[2])
        except (IndexError, ValueError):
            return await interaction.followup.send(
                "❌ Invalid button data.", ephemeral=True
            )

        async with get_session() as session:
            service = DiplomacyService(session)

            # --- HANDLE CANCEL ---
            if action == "cancel":
                stmt = select(PendingBannerCall).where(PendingBannerCall.id == call_id)
                call = (await session.execute(stmt)).scalars().first()

                if call:
                    call.status = "CANCELLED"
                    await session.commit()
                    embed = discord.Embed(
                        title="❌ Banner Call Cancelled", color=discord.Color.red()
                    )
                    await interaction.edit_original_response(embed=embed, view=None)
                else:
                    await interaction.followup.send(
                        "❌ Call not found.", ephemeral=True
                    )
                return

            # --- HANDLE CONFIRM ---
            if action == "confirm":
                # 1. Send Feedback Message
                loading_msg = await interaction.followup.send(
                    "⏳ **Mobilizing the realm...** Calculating routes and spawning armies. This may take a moment.",
                    ephemeral=True,
                )

                try:
                    # 2. Execute Logic
                    march_results, fog_messages = (
                        await service.execute_muster_from_pending_call(call_id)
                    )

                    if not march_results:
                        # If list is empty, it means the call wasn't found or wasn't pending
                        await loading_msg.edit(
                            content="❌ **Error:** Could not execute muster. The call may have expired or already been processed."
                        )
                        return
                    if fog_messages:
                        movements_channel = discord.utils.get(
                            interaction.guild.text_channels, name="general-movements"
                        )
                        if movements_channel:
                            for msg in fog_messages:
                                await movements_channel.send(msg)
                    # 3. Success - Update the original Panel
                    embed = discord.Embed(
                        title="✅ Muster Complete!",
                        description="**Report sent to the player.**\n\n"
                        + "\n".join(march_results[:20]),  # Show first 20 lines
                        color=discord.Color.green(),
                    )
                    if len(march_results) > 20:
                        embed.set_footer(
                            text=f"...and {len(march_results)-20} more updates."
                        )

                    # Edit the GM Panel message to show it's done (remove buttons)
                    await interaction.edit_original_response(embed=embed, view=None)

                    # 4. Notify the Liege Player (if applicable)
                    # We need to fetch the call again (or use the returned data if you modified the service return)
                    # For simplicity, we just confirm to the GM here.
                    await loading_msg.edit(
                        content="✅ **Done!** Armies have been created and orders issued."
                    )

                except Exception as e:
                    import traceback

                    traceback.print_exc()
                    await loading_msg.edit(
                        content=f"❌ **System Error:** An error occurred during muster:\n`{str(e)}`"
                    )

    @commands.command(name="call_levies_sea")
    @commands.check(is_in_house_channel)
    async def call_levies_sea(self, ctx, *, rally_point: str):
        """Initiates a naval levy call for all coastal vassals. Uses Locked Channel IDs."""
        player_wait_msg = await ctx.send(
            f"🌊 **Verifying naval rally point '{rally_point}'...**"
        )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await player_wait_msg.edit(content="❌ No active game found.")

            # --- 1. VALIDATE LOCATION ---
            from app.services.warfare_service import WarfareService

            war_service = WarfareService(session)
            rally_coords = await war_service._get_location_from_db(
                game.game_id, rally_point
            )
            if not rally_coords:
                return await player_wait_msg.edit(
                    content=f"❌ **Invalid Location:** '{rally_point}' not found."
                )

            if not war_service._is_coord_water_or_port(
                int(rally_coords["x"]), int(rally_coords["y"])
            ):
                return await player_wait_msg.edit(
                    content=f"❌ **Invalid Rally Point:** '{rally_point}' is inland. Ships cannot rally there."
                )

            # --- 2. GET PLAYER & HOUSE ---
            stmt_p = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
                .options(
                    selectinload(GamePlayer.house), selectinload(GamePlayer.character)
                )
            )
            liege_player = (await session.execute(stmt_p)).scalars().first()
            if not liege_player or not liege_player.house:
                return await player_wait_msg.edit(
                    content="❌ You do not command a house."
                )

            # --- 3. SPAM CHECK ---
            stmt_check = select(PendingBannerCall).where(
                PendingBannerCall.game_id == game.game_id,
                PendingBannerCall.liege_house_id == liege_player.house.house_id,
                PendingBannerCall.status == "PENDING_APPROVAL",
                PendingBannerCall.call_type == "SEA",
            )
            if (await session.execute(stmt_check)).scalars().first():
                return await player_wait_msg.edit(
                    content="❌ **Hold:** You already have a naval call pending approval."
                )

            # --- 4. SERVICE CALL ---
            from app.services.diplomacy_service import DiplomacyService

            service = DiplomacyService(session)
            success, result_data, player_vassals = await service.prepare_sea_levy_call(
                game.game_id, liege_discord_id=ctx.author.id
            )

            if not success:
                return await player_wait_msg.edit(content=f"❌ {result_data}")
            if not result_data and not player_vassals:
                return await player_wait_msg.edit(
                    content="❌ You have no coastal vassals with available fleets."
                )

            # --- 5. NOTIFY PLAYER VASSALS (USING LOCKED ID) ---
            notified_count = 0
            liege_caller_name = (
                liege_player.character.name
                if liege_player.character
                else ctx.author.display_name
            )

            for pv in player_vassals:
                # Use the ID directly from the service data
                target_chan_id = pv.get("private_channel_id")
                target_user_id = pv.get("user_id")

                channel = (
                    self.bot.get_channel(target_chan_id) if target_chan_id else None
                )

                embed = discord.Embed(
                    title="🌊 A Call for Fleets!", color=discord.Color.blue()
                )
                embed.description = f"**{liege_caller_name}** calls all ships to rally at **{rally_point}**!"
                embed.set_footer(text="Contact your liege for specific orders.")

                try:
                    if channel:
                        await channel.send(f"<@{target_user_id}>", embed=embed)
                        notified_count += 1
                    else:
                        # Fallback to DM if channel ID is missing or deleted
                        member = ctx.guild.get_member(
                            target_user_id
                        ) or await ctx.guild.fetch_member(target_user_id)
                        if member:
                            await member.send(
                                "🌊 **Naval Call:** Your liege summons the fleets!",
                                embed=embed,
                            )
                            notified_count += 1
                except:
                    continue

            # --- 6. CREATE GM PANEL FOR NPC VASSALS ---
            if result_data:
                gm_channel = discord.utils.get(
                    ctx.guild.text_channels, name="gm-alerts"
                )
                if not gm_channel:
                    return await ctx.send(
                        "⚠️ NPC call prepared, but #gm-alerts is missing."
                    )

                vassal_data_for_db = [
                    {
                        "house_id": v["house_id"],
                        "house_name": v["house_name"],
                        "max_troops": v["max_amount"],
                        "percent": v["percent"],
                        "home_x": v.get("home_x", 0),
                        "home_y": v.get("home_y", 0),
                        "source_fleet_id": v.get("source_fleet_id"),
                        "breakdown": v.get("breakdown", ""),
                    }
                    for v in result_data
                ]

                new_pending_call = PendingBannerCall(
                    game_id=game.game_id,
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    message_id=player_wait_msg.id,
                    gm_channel_id=gm_channel.id,
                    gm_message_id=0,
                    liege_house_id=liege_player.house.house_id,
                    rally_point_name=rally_point,
                    vassal_data=vassal_data_for_db,  # Use the mapped data
                    call_type="SEA",
                )
                session.add(new_pending_call)
                await session.flush()

                from app.ui.banner_view import BannerControlView

                view = BannerControlView(new_pending_call.id)
                embed = await view.create_embed(pending_call=new_pending_call)
                gm_msg = await gm_channel.send(embed=embed, view=view)
                new_pending_call.gm_message_id = gm_msg.id
                await session.commit()

            await player_wait_msg.edit(
                content=f"✅ **Naval Call Sent!**\n- Notified {notified_count} player-vassals.\n- GM approval panel sent for {len(result_data)} NPC fleets."
            )

    @commands.command(name="gate_access")
    @commands.check(is_in_house_channel)
    async def gate_access(self, ctx, action: str, *, house_name: str = None):
        """
        Manage who can pass your castles.
        Usage:
        !gate_access add Stark
        !gate_access remove Stark
        !gate_access list
        """
        action = action.lower()

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # Get Player's House
            stmt = select(GamePlayer).where(
                GamePlayer.user_id
                == (
                    select(User.user_id).where(User.discord_id == ctx.author.id)
                ).scalar_subquery(),
                GamePlayer.game_id == game.game_id,
            )
            player = (await session.execute(stmt)).scalars().first()
            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not command a house.")

            # Handle List View
            if action == "list":
                house = await session.get(House, player.claimed_house_id)
                if not house.gate_whitelist:
                    return await ctx.send(
                        "📜 **Gate Whitelist:** None (All armies will be stopped)."
                    )

                # Fetch names
                stmt_names = select(House.name).where(
                    House.house_id.in_(house.gate_whitelist)
                )
                names = (await session.execute(stmt_names)).scalars().all()
                return await ctx.send(f"📜 **Gate Whitelist:**\n" + ", ".join(names))

            # Handle Add/Remove
            if not house_name:
                return await ctx.send(
                    "❌ Please specify a house name. Example: `!gate_access add Stark`"
                )

            service = DiplomacyService(session)
            success, msg = await service.manage_gate_whitelist(
                game.game_id, player.claimed_house_id, house_name, action
            )
            await ctx.send(msg)

    @commands.command(name="vassals")
    @commands.check(is_in_house_channel)
    async def list_vassals(self, ctx):
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
                .options(selectinload(GamePlayer.house))
            )
            player = (await session.execute(stmt)).scalars().first()

            if not player or not player.claimed_house_id:
                return await ctx.send("❌ No house.")

            stmt_v = (
                select(House)
                .where(
                    House.liege_id == player.claimed_house_id,
                    House.game_id == game.game_id,
                )
                .options(selectinload(House.armies), selectinload(House.fiefs))
            )
            vassals = (await session.execute(stmt_v)).scalars().all()

            if not vassals:
                return await ctx.send("🍂 No vassals.")

            lines = []
            for v in vassals:
                troops = sum(a.troop_count for a in v.armies)
                lines.append(f"**{v.name}** | 🏰 {len(v.fiefs)}")

            chunks = [lines[i : i + 10] for i in range(0, len(lines), 10)]
            embeds = [
                discord.Embed(
                    title=f"Banners of {player.house.name}",
                    description="\n".join(c),
                    color=discord.Color.gold(),
                )
                for c in chunks
            ]

            if embeds:
                await ctx.send(
                    embed=embeds[0], view=Paginator(embeds) if len(embeds) > 1 else None
                )

    @commands.command(name="declare_fealty")
    @commands.check(is_in_house_channel)
    async def declare_fealty(self, ctx, *, new_liege: str):
        """
        Swear loyalty to a new House.
        Usage: !declare_fealty Targaryen
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return await ctx.send("❌ You are not in the game.")

            service = DiplomacyService(session)
            success, msg = await service.declare_fealty(
                game.game_id, vassal_user_id=user.user_id, new_liege_name=new_liege
            )

            if success:
                dec_channel = discord.utils.get(
                    ctx.guild.text_channels, name="declarations"
                )
                if dec_channel:
                    embed = discord.Embed(
                        title="📜 Declaration of Fealty",
                        description=msg,
                        color=discord.Color.blue(),
                    )
                    await dec_channel.send(embed=embed)

            await ctx.send(msg)

    @commands.command(name="declare_war")
    @commands.check(is_in_house_channel)
    async def declare_war(self, ctx, target: str, *, reason: str = "Aggression"):
        """
        Declare war on a House or Character.
        (This command currently only sends an announcement, no game state changes in service)
        """
        dec_channel = discord.utils.get(ctx.guild.text_channels, name="declarations")
        if dec_channel:
            embed = discord.Embed(
                title="⚔️ Declaration of War", color=discord.Color.red()
            )
            embed.description = (
                f"**{ctx.author.display_name}** has declared WAR on **{target}**!"
            )
            embed.add_field(name="Casus Belli", value=reason)
            await dec_channel.send(embed=embed)
            await ctx.send("✅ War declared.")
        else:
            await ctx.send("❌ Declarations channel not found.")

    @commands.command(name="disband_levies")
    @commands.check(is_in_house_channel)
    async def disband_levies(self, ctx):
        """
        Returns all NPC levies to their home castles.
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return

            service = DiplomacyService(session)
            success, result = await service.disband_levies(game.game_id, user.user_id)

            if success:
                if not result:
                    await ctx.send("ℹ️ No active levies were found to disband.")
                    return

                embeds = []
                chunk_size = 15
                for i in range(0, len(result), chunk_size):
                    chunk = result[i : i + chunk_size]

                    embed = discord.Embed(
                        title="🏳️ Levies Disbanded",
                        description="\n".join(chunk),
                        color=discord.Color.blue(),
                    )
                    embeds.append(embed)

                paginator_view = Paginator(embeds)
                await ctx.send(embed=paginator_view.embeds[0], view=paginator_view)
            else:
                await ctx.send(result)

    @commands.command(name="meet")
    async def meet(
        self,
        ctx: commands.Context,
        target: discord.Member,
        *,
        location: str = "A Private Setting",
    ):
        """
        Requests a private meeting with another player, creating a channel upon consent.
        Usage: !meet @PlayerName location="The Wolf's Den"
        """
        if target.bot or target == ctx.author:
            return await ctx.send("❌ You cannot meet with a bot or yourself.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game is running on this server.")

            initiator_player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
                .options(
                    selectinload(GamePlayer.character), selectinload(GamePlayer.house)
                )
            )
            target_player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(User.discord_id == target.id, GamePlayer.game_id == game.game_id)
                .options(
                    selectinload(GamePlayer.character), selectinload(GamePlayer.house)
                )
            )

            if not initiator_player or not target_player:
                return await ctx.send(
                    "❌ One or both participants are not active players in the game."
                )

            initiator_name = (
                initiator_player.character.name
                if initiator_player.character
                else (
                    initiator_player.house.name
                    if initiator_player.house
                    else ctx.author.display_name
                )
            )
            target_name = (
                target_player.character.name
                if target_player.character
                else (
                    target_player.house.name
                    if target_player.house
                    else target.display_name
                )
            )

            async def on_accept(interaction: discord.Interaction):
                channel = await self._create_meeting_channel(
                    guild=interaction.guild,
                    member1=ctx.author,
                    member2=target,
                    location_str=location,
                    name1=initiator_name,
                    name2=target_name,
                )

                if channel:
                    await interaction.followup.send(
                        f"✅ The private meeting room has been created: {channel.mention}",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "❌ Failed to create the meeting room. Please check the bot's permissions.",
                        ephemeral=True,
                    )

            proposal_embed = discord.Embed(
                title="📜 A Request for Audience",
                description=f"**{initiator_name}** formally requests a private meeting with **{target_name}**.",
                color=discord.Color.blurple(),
            )

            view = ProposalView(
                initiator=ctx.author,
                consenter=target,
                action_name="Meeting",
                proposal_embed=proposal_embed,
                on_accept_callback=on_accept,
            )

            await ctx.send(
                f"{target.mention}, you have received a proposal.",
                embed=proposal_embed,
                view=view,
            )

    async def _create_meeting_channel(
        self,
        guild: discord.Guild,
        member1: discord.Member,
        member2: discord.Member,
        location_str: str,
        name1: str,
        name2: str,
    ) -> discord.TextChannel | None:
        """A generic helper to create a private meeting channel for two members."""

        category = discord.utils.get(guild.categories, name="Meetings")
        if not category:
            try:
                category = await guild.create_category("Meetings")
            except discord.Forbidden:
                print("ERROR: Bot lacks permission to create categories.")
                return None

        sanitized_name1 = re.sub(r"[^a-zA-Z0-9-]", "", name1.lower().replace(" ", "-"))
        sanitized_name2 = re.sub(r"[^a-zA-Z0-9-]", "", name2.lower().replace(" ", "-"))
        channel_name = f"meet-{sanitized_name1[:15]}-{sanitized_name2[:15]}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member1: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
            member2: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
        }

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"A private meeting between {name1} and {name2}.",
            )
        except discord.Forbidden:
            print(f"ERROR: Bot lacks permission to create channel '{channel_name}'.")
            return None

        welcome_embed = discord.Embed(
            title="Meeting Room",
            description=f"This is a private channel for **{name1}** and **{name2}**.",
            color=discord.Color.dark_grey(),
        )
        welcome_embed.add_field(name="📍 Location", value=location_str)
        await channel.send(
            f"The meeting may now begin. {member1.mention} {member2.mention}",
            embed=welcome_embed,
        )

        return channel

    @commands.command(name="marry")
    async def marry(self, ctx, *, query: str):
        """
        Arrange a marriage between two characters. Requires consent.
        Usage: !marry "[Character A]" to "[Character B]"
        """
        await self._handle_union_proposal(ctx, query, action_name="Marriage", icon="💍")

    @commands.command(name="betroth")
    async def betroth(self, ctx, *, query: str):
        """
        Arrange a betrothal between two characters. Requires consent.
        Usage: !betroth "[Character A]" to "[Character B]"
        """
        await self._handle_union_proposal(
            ctx, query, action_name="Betrothal", icon="📜"
        )

    # --- GM DIPLOMACY COMMANDS ---
    @commands.group(name="gm_diplomacy", invoke_without_command=True)
    @commands.check(is_gm)
    async def gm_diplomacy(self, ctx):
        """GM commands for diplomatic actions for NPCs."""
        await ctx.send(
            "GM Diplomacy Subcommands: `call_banners`, `call_levies_sea`, `declare_fealty`, `declare_war`."
        )

    @gm_diplomacy.command(name="call_banners")
    @commands.check(is_gm)
    async def gm_call_banners(self, ctx, target_house_id: int, *, rally_point: str):
        """GM: Make an NPC house call banners. Uses Locked Channel IDs for notifications."""
        player_wait_msg = await ctx.send(
            f"🦅 **GM Initiated: Preparing banner call for House ID {target_house_id}...**"
        )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await player_wait_msg.edit(content="❌ No active game.")

            # Find the target NPC house
            liege_house_obj = await session.get(House, target_house_id)
            if not liege_house_obj or liege_house_obj.game_id != game.game_id:
                return await player_wait_msg.edit(
                    content=f"❌ Valid NPC House ID {target_house_id} not found."
                )

            # Spam Check
            stmt_check = select(PendingBannerCall).where(
                PendingBannerCall.game_id == game.game_id,
                PendingBannerCall.liege_house_id == target_house_id,
                PendingBannerCall.status == "PENDING_APPROVAL",
                PendingBannerCall.call_type == "LAND",
            )
            if (await session.execute(stmt_check)).scalars().first():
                return await player_wait_msg.edit(
                    content=f"❌ **Hold:** This house already has a pending call."
                )

            # Service Call (GM Override)
            service = DiplomacyService(session)
            success, npc_data, player_vassals = await service.prepare_banner_call(
                game.game_id, acting_house_id=target_house_id, is_gm_override=True
            )

            if not success:
                return await player_wait_msg.edit(content=f"❌ {npc_data}")

            # --- NOTIFY PLAYER VASSALS (LOCKED ID SYSTEM) ---
            sent_count = 0
            liege_name = liege_house_obj.name

            if player_vassals:
                for pv in player_vassals:
                    target_chan_id = pv.get("private_channel_id")
                    target_user_id = pv.get("user_id")
                    vassal_channel = (
                        self.bot.get_channel(target_chan_id) if target_chan_id else None
                    )

                    embed = discord.Embed(
                        title="🦅 A Call to Arms! (GM Initiated)",
                        description=f"**House {liege_name}** has summoned the banners!\n\n"
                        f"My Lord of **{pv['house_name']}**, your liege summons you to rally at **{rally_point}**.",
                        color=discord.Color.dark_red(),
                    )
                    embed.set_footer(text=f"Orders issued by the Crown/GM.")

                    try:
                        if vassal_channel:
                            await vassal_channel.send(
                                f"<@{target_user_id}>", embed=embed
                            )
                            sent_count += 1
                        else:
                            member = ctx.guild.get_member(
                                target_user_id
                            ) or await ctx.guild.fetch_member(target_user_id)
                            if member:
                                await member.send(
                                    f"⚠️ **Banner Call (GM):** Quarters not found.",
                                    embed=embed,
                                )
                                sent_count += 1
                    except:
                        continue

            # --- HANDLE NPC VASSALS (GM Panel) ---
            if npc_data:
                gm_channel = discord.utils.get(
                    ctx.guild.text_channels, name="gm-alerts"
                )
                if not gm_channel:
                    return await player_wait_msg.edit(content="❌ #gm-alerts missing.")

                vassal_data_for_db = [
                    {
                        "house_id": v["house_id"],
                        "house_name": v["house_name"],
                        "max_troops": v["max_amount"],
                        "percent": v["percent"],
                        "home_x": v.get("home_x", 0),
                        "home_y": v.get("home_y", 0),
                        "breakdown": v.get("breakdown", ""),
                    }
                    for v in npc_data
                ]

                new_pending_call = PendingBannerCall(
                    game_id=game.game_id,
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    message_id=player_wait_msg.id,
                    gm_channel_id=gm_channel.id,
                    gm_message_id=0,
                    liege_house_id=target_house_id,
                    rally_point_name=rally_point,
                    vassal_data=vassal_data_for_db,
                    call_type="LAND",
                )
                session.add(new_pending_call)
                await session.flush()

                view = BannerControlView(new_pending_call.id)
                embed = await view.create_embed(
                    pending_call=new_pending_call, gm_initiator=ctx.author
                )
                gm_panel_msg = await gm_channel.send(embed=embed, view=view)
                new_pending_call.gm_message_id = gm_panel_msg.id
                await session.commit()

            await player_wait_msg.edit(
                content=f"✅ **GM Call Sent!** Notified {sent_count} player vassals for House {liege_name}."
            )

    @gm_diplomacy.command(name="call_levies_sea")
    @commands.check(is_gm)
    async def gm_call_levies_sea(self, ctx, target_house_id: int, *, rally_point: str):
        """GM: Make an NPC house call naval levies. Uses Locked Channel IDs."""
        player_wait_msg = await ctx.send(
            f"🌊 **Verifying naval rally point '{rally_point}'...**"
        )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await player_wait_msg.edit(content="❌ No active game.")

            liege_house_obj = await session.get(House, target_house_id)
            if not liege_house_obj:
                return await player_wait_msg.edit(content="❌ House not found.")

            # Spam Check
            stmt_check = select(PendingBannerCall).where(
                PendingBannerCall.game_id == game.game_id,
                PendingBannerCall.liege_house_id == target_house_id,
                PendingBannerCall.status == "PENDING_APPROVAL",
                PendingBannerCall.call_type == "SEA",
            )
            if (await session.execute(stmt_check)).scalars().first():
                return await player_wait_msg.edit(
                    content="❌ **Hold:** Naval call already pending."
                )

            # Service Call
            service = DiplomacyService(session)
            success, result_data, player_vassals = await service.prepare_sea_levy_call(
                game.game_id, acting_house_id=target_house_id, is_gm_override=True
            )

            if not success:
                return await player_wait_msg.edit(content=f"❌ {result_data}")

            # --- NOTIFY PLAYERS (USING LOCKED ID) ---
            notified_count = 0
            for pv in player_vassals:
                target_chan_id = pv.get("private_channel_id")
                target_user_id = pv.get("user_id")
                channel = (
                    self.bot.get_channel(target_chan_id) if target_chan_id else None
                )

                embed = discord.Embed(
                    title="🌊 A Call for Fleets! (GM Initiated)",
                    color=discord.Color.blue(),
                )
                embed.description = f"**House {liege_house_obj.name}** calls the fleets to rally at **{rally_point}**!"

                try:
                    if channel:
                        await channel.send(f"<@{target_user_id}>", embed=embed)
                        notified_count += 1
                    else:
                        member = ctx.guild.get_member(
                            target_user_id
                        ) or await ctx.guild.fetch_member(target_user_id)
                        if member:
                            await member.send(
                                "🌊 **Naval Call (GM):** Quarters not found.",
                                embed=embed,
                            )
                            notified_count += 1
                except:
                    continue

            # --- GM PANEL ---
            if result_data:
                gm_channel = discord.utils.get(
                    ctx.guild.text_channels, name="gm-alerts"
                )
                if not gm_channel:
                    return await ctx.send("❌ #gm-alerts missing.")

                vassal_data_for_db = [
                    {
                        "house_id": v["house_id"],
                        "house_name": v["house_name"],
                        "max_troops": v["max_amount"],
                        "percent": v["percent"],
                        "home_x": v.get("home_x", 0),
                        "home_y": v.get("home_y", 0),
                    }
                    for v in result_data
                ]

                new_pending_call = PendingBannerCall(
                    game_id=game.game_id,
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    message_id=player_wait_msg.id,
                    gm_channel_id=gm_channel.id,
                    gm_message_id=0,
                    liege_house_id=target_house_id,
                    rally_point_name=rally_point,
                    vassal_data=vassal_data_for_db,
                    call_type="SEA",
                )
                session.add(new_pending_call)
                await session.flush()

                view = BannerControlView(new_pending_call.id)
                embed = await view.create_embed(
                    pending_call=new_pending_call, gm_initiator=ctx.author
                )
                gm_panel_msg = await gm_channel.send(embed=embed, view=view)
                new_pending_call.gm_message_id = gm_panel_msg.id
                await session.commit()

            await player_wait_msg.edit(
                content=f"✅ **GM Naval Call Sent!** Notified {notified_count} player vassals."
            )

    @gm_diplomacy.command(name="declare_fealty")
    @commands.check(is_gm)
    async def gm_declare_fealty(
        self, ctx, vassal_house_id: int, *, new_liege_name: str
    ):
        """GM: Make an NPC house declare fealty to another. Usage: !gm_diplomacy declare_fealty [VassalHouseID] [NewLiegeName]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = DiplomacyService(session)
            success, msg = await service.declare_fealty(
                game_id=game.game_id,
                vassal_house_id=vassal_house_id,
                new_liege_name=new_liege_name,
                is_gm_override=True,
            )

            # Post to declarations channel
            if success:
                dec_channel = discord.utils.get(
                    ctx.guild.text_channels, name="declarations"
                )
                if dec_channel:
                    embed = discord.Embed(
                        title="📜 GM Initiated Declaration of Fealty",
                        description=msg,
                        color=discord.Color.blue(),
                    )
                    await dec_channel.send(embed=embed)

            await ctx.send(f"✅ GM Command: {msg}")

    @gm_diplomacy.command(name="declare_war")
    @commands.check(is_gm)
    async def gm_declare_war(
        self,
        ctx,
        aggressor_house_id: int,
        target_house_name: str,
        *,
        reason: str = "Aggression",
    ):
        """GM: Make an NPC house declare war on another. Usage: !gm_diplomacy declare_war [AggressorHouseID] [TargetHouseName] [Reason='Aggression']"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            aggressor_house = await session.get(House, aggressor_house_id)
            if not aggressor_house:
                return await ctx.send(
                    f"❌ Aggressor House ID {aggressor_house_id} not found."
                )
            if aggressor_house.game_id != game.game_id:
                return await ctx.send(
                    f"❌ Aggressor House ID {aggressor_house_id} does not belong to this game."
                )

            dec_channel = discord.utils.get(
                ctx.guild.text_channels, name="declarations"
            )
            if dec_channel:
                embed = discord.Embed(
                    title="⚔️ GM Initiated Declaration of War", color=discord.Color.red()
                )
                embed.description = f"On behalf of **House {aggressor_house.name}**, GM {ctx.author.display_name} has declared WAR on **{target_house_name}**!"
                embed.add_field(name="Casus Belli", value=reason)
                await dec_channel.send(embed=embed)
                await ctx.send(
                    f"✅ GM Command: War declared on behalf of House {aggressor_house.name}."
                )
            else:
                await ctx.send("❌ Declarations channel not found.")

    @commands.command(name="gm_gate_access")
    @commands.has_permissions(administrator=True)
    async def gm_gate_access(
        self, ctx, host_house_id: int, action: str, target_house_id: int
    ):
        """
        GM: Manage gate access for NPC houses.
        Usage: !gm_gate_access [HostID] [add/remove] [TargetID]
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = DiplomacyService(session)
            success, msg = await service.manage_gate_whitelist(
                game.game_id,
                host_house_id,
                target_house_id,  # Passing ID directly for GMs is usually safer/easier
                action.lower(),
            )
            await ctx.send(f"🤖 GM Command: {msg}")


async def setup(bot):
    await bot.add_cog(DiplomacyCog(bot))
