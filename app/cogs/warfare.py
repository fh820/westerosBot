import discord
from discord.ext import commands, tasks
from sqlalchemy import select, or_, func, delete, text
from sqlalchemy.orm import selectinload
import asyncio
import json
import os
import re
import datetime
import redis.asyncio as redis

from app.db.db_manager import get_session
from app.db.models import (
    User,
    Army,
    GamePlayer,
    House,
    PendingInteraction,
    Game,
    Fief,
    Character,
)
from app.db.repositories import GameRepo, ArmyRepo, FiefRepo

from app.services.warfare_service import WarfareService, FOG_OF_WAR_THRESHOLD
from app.tasks.light_tasks import resolve_army_arrival, handle_gate_response
from app.ui.march_view import ArmySelectView, JourneyArmySelectView
from app.ui.paginator import Paginator
from app.ui.sail_view import FleetSelectView, DirectSailView
from app.ui.redirect_view import RedirectSelectView
from app.ui.coalition_view import CoalitionConsentView
from app.ui.gate_view import GateActionView
from app.ui.interaction_view import InteractionView
from app.ui.autobattle_view import AutoBattleControlView
from app.ui.gm_march_view import GMMarchArmySelectView
from app.checks import (
    is_in_house_channel,
    recruitment_is_enabled,
)
from app.services.common import slugify

fief_cache = {}


async def is_gm(ctx):
    """
    Checks if the command author is a registered User and has the is_gm flag.
    Also returns True if the author is the guild owner or has administrator permissions.
    """
    # First, check for Discord-level admin permissions for immediate access
    if ctx.author.guild_permissions.administrator:
        return True

    # If not an admin, check the database flag
    async with get_session() as session:
        user = await session.scalar(
            select(User).where(User.discord_id == ctx.author.id)
        )
        # This now safely handles the case where 'user' is None
        return user and user.is_gm


# --- UTILITY FUNCTIONS ---
def parse_loose_json(text: str) -> dict:
    """
    A robust parser that handles unquoted keys AND values.
    Input:  { from: Winterfell, to: "Moat Cailin", units: {inf: 100} }
    Output: {'from': 'Winterfell', 'to': 'Moat Cailin', 'units': {'inf': 100}}
    """
    text = text.strip()
    # Remove outer braces if present
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]

    # 1. Split by comma, respecting nested braces
    items = []
    buffer = ""
    depth = 0
    for char in text:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1

        if char == "," and depth == 0:
            items.append(buffer.strip())
            buffer = ""
        else:
            buffer += char
    if buffer.strip():
        items.append(buffer.strip())

    result = {}
    for item in items:
        if ":" not in item:
            continue

        # Split into Key and Value
        key, val = item.split(":", 1)
        key = key.strip().strip('"').strip("'")  # Clean key
        val = val.strip()

        # Logic to determine Value Type

        # A. Nested Object (Recursion)
        if val.startswith("{"):
            val = parse_loose_json(val)
        # B. Integer
        elif val.isdigit():
            val = int(val)
        # C. Boolean
        elif val.lower() == "true":
            val = True
        elif val.lower() == "false":
            val = False
        # D. String (Remove quotes if they exist, keep raw text if they don't)
        else:
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]

        result[key] = val

    return result


def calculate_army_size_from_units(units_input) -> int:
    """Calculates total army size from various unit input formats."""
    if isinstance(units_input, int):
        return units_input
    if isinstance(units_input, str) and units_input.isdigit():
        return int(units_input)
    if isinstance(units_input, dict):
        return sum(filter(lambda v: isinstance(v, int), units_input.values()))
    return 1000  # Default fallback if unknown


# --- MAIN COG ---
class WarfareCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis = None
        self.pubsub = None
        asyncio.create_task(self.connect_and_listen())

    # Moved from global to be a method of WarfareCog
    async def get_discord_user_from_house(self, session, game_id, house_id):
        """Helper to find the Discord User associated with a House ID."""
        stmt = (
            select(User)
            .join(GamePlayer)
            .where(
                GamePlayer.game_id == game_id,
                GamePlayer.claimed_house_id == house_id,
                GamePlayer.is_primary == True,
            )
        )
        user_db = (await session.execute(stmt)).scalars().first()
        if user_db:
            return self.bot.get_user(user_db.discord_id)
        return None

    def cog_unload(self):
        self.redis_listener.cancel()
        if self.redis:
            asyncio.create_task(self.redis.close())

    async def connect_and_listen(self):
        """
        Keeps a persistent connection to Redis and restarts the listener on disconnect.
        This is the main connection management loop.
        """
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                print("🟢 Connecting to Redis...")
                self.redis = redis.from_url(self.redis_url, decode_responses=True)
                self.pubsub = self.redis.pubsub()
                await self.pubsub.subscribe("westeros_bot_events")
                print("✅ Redis Listener Subscribed.")
                self.redis_listener.start()
                return

            except redis.exceptions.ConnectionError as e:
                print(f"⚠️ Could not connect to Redis: {e}. Retrying in 10 seconds...")
                if self.redis_listener.is_running():
                    self.redis_listener.stop()
                if self.redis:
                    await self.redis.close()
                self.redis = None
                self.pubsub = None
                await asyncio.sleep(10)

    @tasks.loop(seconds=1.0)
    async def redis_listener(self):
        """
        This task's ONLY job is to listen for messages on the established connection.
        It should NOT create a new connection.
        """
        try:
            async for message in self.pubsub.listen():
                if message and message["type"] == "message":
                    asyncio.create_task(self.process_message(message["data"]))
        except Exception as e:
            print(f"⚠️ Redis listener error: {e}. Attempting to reconnect...")
            self.redis_listener.stop()
            asyncio.create_task(self.connect_and_listen())

    async def process_message(self, raw_data):
        """Processes the message without blocking the listener."""
        try:
            data = json.loads(raw_data)
            if data["type"] == "ARRIVAL":
                await self.handle_arrival_notification(data)
            elif data["type"] == "INTERCEPTION":
                await self.handle_interception_notification(data)
            elif data["type"] in ["PATH_READY", "PATH_FAILED"]:
                await self.handle_path_notification(data)
            elif data["type"] == "BANKRUPTCY_ALERT":
                await self.handle_bankruptcy_notification(data)
            elif data["type"] == "BANNER_REPORT":
                await self.handle_banner_report(data)
            elif data["type"] == "GATE_ALERT":
                await self.handle_gate_alert(data)
            elif data["type"] == "GATE_RESPONSE":
                if data.get("action") == "GRANT":
                    async with get_session() as session:
                        service = WarfareService(session)
                        success, response, fog_msg = (
                            await service.resume_march_from_gate(data["army_id"])
                        )
                        if success:
                            print(
                                f"✅ March resumed for Army {data['army_id']}. New ETA: {response['time']}"
                            )
                        else:
                            print(
                                f"❌ Failed to resume march for Army {data['army_id']}: {response}"
                            )
            elif data["type"] == "PASSAGE_DENIED":
                await self.handle_passage_denied(data)
            elif data["type"] == "PROMPT_INTERACTION":
                await self.handle_prompt_interaction(data)
            elif data["type"] in [
                "INTERACTION_BATTLE",
                "INTERACTION_MEETING",
                "INTERACTION_ENDED",
            ]:
                await self.handle_interaction_resolution(data)
            elif data["type"] == "PROMPT_AUTOBATTLE":
                await self.handle_prompt_autobattle(data)
            elif data["type"] == "BATTLE_REPORT_ROUND":
                await self.handle_battle_round_report(data)
            elif data["type"] == "BATTLE_REPORT_FINAL":
                await self.handle_battle_final_report(data)
            elif data["type"] == "BATTLE_STARTED":
                await self.handle_battle_started(data)
        except Exception as e:
            print(f"❌ Error processing Redis message: {e}")

    async def handle_prompt_autobattle(self, data):
        """
        Receives the prompt from the Celery worker and posts the GM choice UI.
        """
        guild = self.bot.get_guild(data.get("guild_id"))
        if not guild:
            return

        gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
        if not gm_channel:
            print(
                "ERROR: Could not find #gm-alerts channel to post auto-battle prompt."
            )
            return

        embed = discord.Embed(
            title="🛡️ Auto-Battle Pending",
            description="A battle has been triggered by player actions. You have **15 minutes** to intervene before the auto-battle begins.",
            color=discord.Color.dark_red(),
        )
        embed.add_field(
            name="Attacker", value=data.get("attacker_name", "Unknown"), inline=True
        )
        embed.add_field(
            name="Defender", value=data.get("defender_name", "Unknown"), inline=True
        )
        embed.set_footer(text=f"Battle ID: {data.get('battle_id')}")

        view = AutoBattleControlView(
            battle_id=data.get("battle_id"),
            resolver_task_id=data.get("resolver_task_id"),
        )

        gm_role = discord.utils.get(guild.roles, name="Game Master")
        mention = gm_role.mention if gm_role else ""

        try:
            await gm_channel.send(content=f"🚨 {mention}", embed=embed, view=view)
        except Exception as e:
            print(f"Error sending auto-battle prompt to GM channel: {e}")

    async def handle_battle_started(self, data):
        """Posts an announcement that an auto-battle has begun."""
        guild = self.bot.get_guild(data.get("guild_id"))
        if not guild:
            return

        battle_channel = discord.utils.get(guild.text_channels, name="battle-reports")
        if not battle_channel:
            return

        embed = discord.Embed(
            title="⚔️ A Skirmish Has Erupted!",
            description=f"Forces from **House {data['attacker_house']}** under **{data['attacker_name']}** have engaged with the army of **House {data['defender_house']}** led by **{data['defender_name']}**!",
            color=discord.Color.dark_orange(),
        )
        embed.set_footer(
            text=f"Battle ID: {data.get('battle_id')}. Reports will follow."
        )

        try:
            await battle_channel.send(embed=embed)
        except discord.Forbidden:
            print(f"ERROR: Bot lacks permission to post in #{battle_channel.name}")

    async def handle_battle_round_report(self, data):
        """Posts a simplified result of a single auto-battle round with NO casualty fields."""
        guild = self.bot.get_guild(data.get("guild_id"))
        if not guild:
            return

        battle_channel = discord.utils.get(guild.text_channels, name="battle-reports")
        if not battle_channel:
            return

        scores = data.get("scores", {})

        embed = discord.Embed(
            title=f"⚔️ Round {data.get('round_number', '?')} Analysis ({scores.get('attacker',0)} - {scores.get('defender',0)})",
            description=data.get("roll_msg", "Roll details unavailable."),
            color=discord.Color.dark_grey(),
        )
        embed.set_footer(text=f"Battle ID: {data.get('battle_id')}")

        try:
            await battle_channel.send(embed=embed)
        except discord.Forbidden:
            print(f"ERROR: Bot lacks permission to post in #{battle_channel.name}")

    async def handle_battle_final_report(self, data):
        """Posts the final conclusion of an auto-battle."""
        print("\n--- DEBUG: handle_battle_final_report START ---")
        guild_id = data.get("guild_id")
        battle_id = data.get("battle_id")

        # --- FIX STARTS HERE: Handle Tuple vs String ---
        raw_report = data.get("report_string")

        if isinstance(raw_report, (list, tuple)):
            # If it came as ('Text', GuildID), grab just the text (index 0)
            report_string = raw_report[0]
        else:
            # It's already a clean string
            report_string = raw_report
        # --- FIX ENDS HERE ---

        print(
            f"  - Received final report for Battle ID: {battle_id} in Guild ID: {guild_id}"
        )

        if not guild_id or not battle_id or not report_string:
            print(
                "  - CRITICAL ERROR: Payload is missing guild_id, battle_id, or report_string. Aborting."
            )
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            print(
                f"  - CRITICAL ERROR: Bot could not find Guild with ID {guild_id}. Aborting."
            )
            return

        print(f"  - Found Guild: '{guild.name}'")

        battle_channel = discord.utils.get(guild.text_channels, name="battle-reports")
        if not battle_channel:
            print(
                f"  - CRITICAL ERROR: Could not find #battle-reports channel in '{guild.name}'. Aborting."
            )
            return

        print(f"  - Found Channel: '#{battle_channel.name}'")

        embed = discord.Embed(
            title=f"🏁 Battle Concluded! (ID: {battle_id})",
            description=report_string,  # Now guaranteed to be just the text
            color=discord.Color.green(),
        )

        try:
            await battle_channel.send(embed=embed)
            print(f"  - SUCCESS: Posted final report for Battle ID: {battle_id}")
        except discord.Forbidden:
            print(
                f"  - DISCORD ERROR: Bot lacks permission to post final report in #{battle_channel.name}"
            )
        except Exception as e:
            print(
                f"  - UNEXPECTED ERROR: An error occurred while posting the final report: {e}"
            )

        print("--- DEBUG: handle_battle_final_report END ---\n")

    async def handle_passage_denied(self, payload: dict):
        """
        Notifies an attacker in their locked quarters that their passage was denied.
        """
        guild_id = payload.get("guild_id")
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        army_id = payload.get("army_id")
        gate_name = payload.get("gate_name")
        denied_by = payload.get("denied_by")

        async with get_session() as session:
            # 1. Fetch Army and associated House
            army = await session.get(Army, army_id, options=[selectinload(Army.house)])
            if not army or not army.house:
                print(f"[ERROR] Could not find army/house for ID {army_id}")
                return

            # 2. Find the GamePlayer (The marcher)
            stmt = (
                select(GamePlayer)
                .join(User)
                .where(
                    GamePlayer.claimed_house_id == army.house_id,
                    GamePlayer.game_id == army.game_id,
                )
                .options(selectinload(GamePlayer.user))
            )
            player = (await session.execute(stmt)).scalars().first()

            # --- BRANCH A: NPC Marcher ---
            if not player or not player.user:
                gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
                if gm_channel:
                    embed_npc = discord.Embed(
                        title="❌ NPC March Halted: Passage Denied",
                        description=f"NPC army **{army.commander_name}** ({army.house.name}) was denied passage at **{gate_name}** by **{denied_by}**.",
                        color=discord.Color.orange(),
                    )
                    await gm_channel.send(embed=embed_npc)
                return

            # --- BRANCH B: Player Marcher (Use Locked ID) ---
            # Use the helper to find the quarters
            target_channel = await self.get_player_channel(
                session, guild, army.house_id, army.game_id
            )

            if target_channel:
                embed = discord.Embed(
                    title="❌ March Halted: Passage Denied",
                    description=f"Your army, **{army.commander_name}**, has been denied passage at **{gate_name}** by **{denied_by}**.",
                    color=discord.Color.red(),
                )
                embed.set_footer(
                    text="The army has stopped moving and awaits new orders."
                )

                try:
                    await target_channel.send(
                        content=f"<@{player.user.discord_id}>", embed=embed
                    )
                    return  # Success
                except:
                    pass

            # --- FALLBACK: GM Alerts if Player Channel missing ---
            gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
            if gm_channel:
                embed_gm = discord.Embed(
                    title="❌ Player March Halted (Channel Missing/Not Locked)",
                    description=(
                        f"Army **{army.commander_name}** of **{army.house.name}** (<@{player.user.discord_id}>) "
                        f"was denied passage at **{gate_name}** by **{denied_by}**.\n\n"
                        f"⚠️ **Note:** Attempted to notify player, but their private channel is not locked or was not found."
                    ),
                    color=discord.Color.red(),
                )
                await gm_channel.send(embed=embed_gm)

    async def get_player_channel(
        self, session, guild: discord.Guild, house_id: int, game_id: int
    ):
        """
        Helper to find a player's locked private channel ID from the database.
        """
        stmt = select(GamePlayer).where(
            GamePlayer.claimed_house_id == house_id, GamePlayer.game_id == game_id
        )
        player = (await session.execute(stmt)).scalars().first()

        if player and player.private_channel_id:
            return self.bot.get_channel(player.private_channel_id)

        # Fallback to slug lookup for legacy players who haven't locked an ID yet
        if player:
            # We need the house name for the slug
            house = await session.get(House, house_id)
            if house:
                slug = slugify(house.name)
                return discord.utils.get(guild.text_channels, name=f"{slug}-quarters")

        return None

    async def handle_prompt_interaction(self, data: dict):
        """
        Sends the Interaction UI (Battle/Meet/March On) to both parties.
        Uses Locked Channel IDs for players and GM Alerts for NPCs.
        """
        interaction_id = data["interaction_id"]

        async with get_session() as session:
            interaction = await session.get(
                PendingInteraction,
                interaction_id,
                options=[
                    selectinload(PendingInteraction.army1)
                    .selectinload(Army.house)
                    .selectinload(House.game),
                    selectinload(PendingInteraction.army2).selectinload(Army.house),
                ],
            )

            if not interaction:
                return

            army1 = interaction.army1
            army2 = interaction.army2
            game_id = army1.game_id
            guild = self.bot.get_guild(army1.house.game.guild_id)
            if not guild:
                return

            gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")

            # --- Helper: Get Owner Data ---
            async def _get_owner_context(session, army_obj):
                stmt = (
                    select(User.discord_id, GamePlayer.private_channel_id)
                    .join(GamePlayer, User.user_id == GamePlayer.user_id)
                    .where(
                        GamePlayer.claimed_house_id == army_obj.house_id,
                        GamePlayer.game_id == army_obj.game_id,
                    )
                )
                res = (await session.execute(stmt)).first()
                return res  # Returns (discord_id, private_channel_id) or None

            owner1 = await _get_owner_context(session, army1)
            owner2 = await _get_owner_context(session, army2)

            # ====================================================
            #      SIDE 1: The Marcher (Army 1)
            # ====================================================
            view1 = InteractionView(
                interaction_id=interaction.id, for_army_id=army1.army_id
            )

            # Find the best channel for Army 1
            chan1 = None
            if owner1 and owner1.private_channel_id:
                chan1 = self.bot.get_channel(owner1.private_channel_id)
            if not chan1:  # Fallback to helper (Slug match for legacy)
                chan1 = await self.get_player_channel(
                    session, guild, army1.house_id, game_id
                )

            if owner1:
                # PLAYER SIDE 1
                target_chan = chan1 or gm_channel
                ping = f"<@{owner1.discord_id}>"
                embed1 = discord.Embed(
                    title="⚔️ Contact Imminent: Active Mover",
                    description=f"Your **{army1.commander_name}** has intercepted **{army2.commander_name}** ({army2.house.name}).\n\n"
                    f"**Choices:**\n"
                    f"⚔️ **Battle:** Engage the enemy.\n"
                    f"🤝 **Meeting:** Request a private audience.\n"
                    f"👣 **March On:** Ignore them and continue.",
                    color=discord.Color.orange(),
                )
                if not chan1:
                    embed1.set_footer(
                        text="⚠️ Private quarters missing; sent to GM Alerts fallback."
                    )
            else:
                # NPC SIDE 1 (GM Alerts)
                target_chan = gm_channel
                ping = "🔔 **NPC INTERACTION (Marcher)**"
                embed1 = discord.Embed(
                    title="🤖 GM Action: NPC Interception",
                    description=f"NPC **{army1.commander_name}** ({army1.house.name}) has intercepted {army2.commander_name}.\n"
                    f"GMs must choose the NPC's stance.",
                    color=discord.Color.blue(),
                )

            if target_chan:
                msg1 = await target_chan.send(content=ping, embed=embed1, view=view1)
                interaction.army1_channel_id = target_chan.id
                interaction.army1_message_id = msg1.id

            # ====================================================
            #      SIDE 2: The Target (Army 2)
            # ====================================================
            view2 = InteractionView(
                interaction_id=interaction.id, for_army_id=army2.army_id
            )

            # Find the best channel for Army 2
            chan2 = None
            if owner2 and owner2.private_channel_id:
                chan2 = self.bot.get_channel(owner2.private_channel_id)
            if not chan2:
                chan2 = await self.get_player_channel(
                    session, guild, army2.house_id, game_id
                )

            if owner2:
                # PLAYER SIDE 2
                target_chan = chan2 or gm_channel
                ping = f"<@{owner2.discord_id}>"
                embed2 = discord.Embed(
                    title="🛡️ Contact Imminent: Intercepted!",
                    description=f"Your **{army2.commander_name}** is being intercepted by **{army1.commander_name}** ({army1.house.name}).\n\n"
                    f"**Choices:**\n"
                    f"⚔️ **Battle:** Engage the aggressor.\n"
                    f"🤝 **Meeting:** Request a private audience.\n"
                    f"👣 **Ignore:** Stand your ground / Continue.",
                    color=discord.Color.red(),
                )
            else:
                # NPC SIDE 2 (GM Alerts)
                target_chan = gm_channel
                ping = "⚠️ **NPC INTERACTION (Defender)**"
                embed2 = discord.Embed(
                    title="🤖 GM Action: NPC Intercepted",
                    description=f"NPC **{army2.commander_name}** ({army2.house.name}) is being engaged by {army1.commander_name}.\n"
                    f"GMs must choose the NPC's stance.",
                    color=discord.Color.blue(),
                )

            if target_chan:
                msg2 = await target_chan.send(content=ping, embed=embed2, view=view2)
                interaction.army2_channel_id = target_chan.id
                interaction.army2_message_id = msg2.id

            await session.commit()

    async def handle_interaction_resolution(self, data: dict):
        """
        Finalizes an interaction (Battle, Meeting, or March On).
        Uses Locked Channel IDs to update player UI.
        """
        interaction_id = data.get("interaction_id")
        outcome = data.get("type")
        if not interaction_id or not outcome:
            return

        async with get_session() as session:
            # Load interaction with full relationship data
            interaction = await session.get(
                PendingInteraction,
                interaction_id,
                options=[
                    selectinload(PendingInteraction.army1)
                    .selectinload(Army.house)
                    .selectinload(House.game),
                    selectinload(PendingInteraction.army2).selectinload(Army.house),
                ],
            )
            if not interaction:
                return

            guild = self.bot.get_guild(interaction.army1.house.game.guild_id)
            if not guild:
                return

            # --- OUTCOME: MEETING ---
            if outcome == "INTERACTION_MEETING":
                # Helper: Find primary player Discord ID and their private quarters ID
                async def get_player_context(house_id, game_id):
                    stmt = (
                        select(User.discord_id, GamePlayer.private_channel_id)
                        .join(GamePlayer)
                        .where(
                            GamePlayer.claimed_house_id == house_id,
                            GamePlayer.game_id == game_id,
                            GamePlayer.is_primary == True,
                        )
                    )
                    return (await session.execute(stmt)).first()

                p1_ctx = await get_player_context(
                    interaction.army1.house_id, interaction.game_id
                )
                p2_ctx = await get_player_context(
                    interaction.army2.house_id, interaction.game_id
                )

                member1 = (
                    await guild.fetch_member(p1_ctx.discord_id) if p1_ctx else None
                )
                member2 = (
                    await guild.fetch_member(p2_ctx.discord_id) if p2_ctx else None
                )

                if not member1 or not member2:
                    # If one party is an NPC, a player meeting channel cannot be auto-created
                    gm_channel = discord.utils.get(
                        guild.text_channels, name="gm-alerts"
                    )
                    if gm_channel:
                        await gm_channel.send(
                            f"⚠️ **Meeting Required:** Interaction {interaction_id} resolved to MEETING, "
                            f"but one party is an NPC or missing. GMs must facilitate."
                        )
                    return

                # Create the channel via DiplomacyCog
                diplomacy_cog = self.bot.get_cog("DiplomacyCog")
                if diplomacy_cog:
                    loc_str = f"({int(interaction.location_x)}, {int(interaction.location_y)})"
                    meeting_chan = await diplomacy_cog._create_meeting_channel(
                        guild=guild,
                        member1=member1,
                        member2=member2,
                        location_str=loc_str,
                        name1=interaction.army1.house.name,
                        name2=interaction.army2.house.name,
                    )

                    # Notify players in their LOCKED quarters that the channel is ready
                    if meeting_chan:
                        embed = discord.Embed(
                            title="🤝 Meeting Arranged",
                            description=f"Your request for an audience has been accepted. "
                            f"Proceed to {meeting_chan.mention} to begin negotiations.",
                            color=discord.Color.blue(),
                        )
                        for ctx_obj in [p1_ctx, p2_ctx]:
                            if ctx_obj and ctx_obj.private_channel_id:
                                q_chan = self.bot.get_channel(
                                    ctx_obj.private_channel_id
                                )
                                if q_chan:
                                    await q_chan.send(embed=embed)

            # --- OUTCOME: MARCH ON / IGNORE ---
            elif outcome == "INTERACTION_ENDED":
                embed = discord.Embed(
                    title="🚩 Contact Resolved: March Resumed",
                    description="The armies have bypassed one another or the encounter has timed out. "
                    "Your forces continue toward their destination.",
                    color=discord.Color.light_grey(),
                )

                # Send resolution to the EXACT channels where the prompts were sent
                # (These IDs were saved during handle_prompt_interaction using our new logic)
                channels_to_notify = []
                if interaction.army1_channel_id:
                    channels_to_notify.append(interaction.army1_channel_id)
                if interaction.army2_channel_id:
                    channels_to_notify.append(interaction.army2_channel_id)

                for chan_id in set(channels_to_notify):
                    channel = self.bot.get_channel(chan_id)
                    if channel:
                        try:
                            await channel.send(embed=embed)
                        except:
                            pass

            # --- OUTCOME: BATTLE ---
            elif outcome == "INTERACTION_BATTLE":
                # initiate_auto_battle worker task handles the reports for this
                print(
                    f"[DEBUG] Interaction {interaction_id} escalating to Battle sequence."
                )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """
        Handles button clicks for interception interactions (Battle/Meet/March).
        Supports Player owners and GM/Admin overrides for NPCs.
        """
        custom_id = interaction.data.get("custom_id")
        if not custom_id or not custom_id.startswith("interaction_"):
            return

        # 1. Parse Button Data (FIXED)
        try:
            # Strip the prefix first: "interaction_MARCH_ON_1_50" -> "MARCH_ON_1_50"
            raw_data = custom_id[len("interaction_") :]

            # Split from the RIGHT, max 2 splits.
            # "MARCH_ON_1_50" -> ['MARCH_ON', '1', '50']
            # "BATTLE_1_50"   -> ['BATTLE', '1', '50']
            parts = raw_data.rsplit("_", 2)

            if len(parts) != 3:
                raise ValueError("Invalid ID format")

            choice = parts[0]  # "MARCH_ON"
            interaction_id = int(parts[1])
            army_id = int(parts[2])

        except (IndexError, ValueError):
            return

        await interaction.response.defer(ephemeral=True)

        async with get_session() as session:
            # 2. Load Interaction State
            pending_interaction = await session.get(
                PendingInteraction,
                interaction_id,
                options=[
                    selectinload(PendingInteraction.army1),
                    selectinload(PendingInteraction.army2),
                ],
            )

            if not pending_interaction or pending_interaction.status != "PENDING":
                return await interaction.followup.send(
                    "❌ This encounter has already been resolved or has expired.",
                    ephemeral=True,
                )

            # 3. Determine target army and verify authority
            is_authorized = False
            target_army = None

            if army_id == pending_interaction.army1_id:
                target_army = pending_interaction.army1
            elif army_id == pending_interaction.army2_id:
                target_army = pending_interaction.army2

            if not target_army:
                return await interaction.followup.send(
                    "❌ Error: Targeted army not found.", ephemeral=True
                )

            # A. Check if the user is the GM/Admin
            if interaction.user.guild_permissions.administrator:
                is_authorized = True
            else:
                # B. Check if the user is the primary owner of the house
                stmt_owner = (
                    select(User.discord_id)
                    .join(GamePlayer)
                    .where(
                        GamePlayer.claimed_house_id == target_army.house_id,
                        GamePlayer.game_id == target_army.game_id,
                        GamePlayer.is_primary == True,
                    )
                )
                owner_discord_id = (await session.execute(stmt_owner)).scalar()
                if owner_discord_id == interaction.user.id:
                    is_authorized = True

            if not is_authorized:
                return await interaction.followup.send(
                    "❌ **Authority Denied:** You do not have the right to issue orders to this host.",
                    ephemeral=True,
                )

            # 4. Save the Choice
            if army_id == pending_interaction.army1_id:
                pending_interaction.army1_choice = choice
            else:
                pending_interaction.army2_choice = choice

            await session.commit()

            # 5. UI Cleanup: Disable buttons and update embed
            clean_choice = (
                choice.replace("MARCH_ON", "CONTINUE MARCH").replace("_", " ").title()
            )

            # Update the specific message the user clicked
            # We create a "Disabled" version of the view to show the order is locked in
            from app.ui.interaction_view import InteractionView

            disabled_view = InteractionView(interaction_id, army_id)
            for item in disabled_view.children:
                item.disabled = True

            # Edit the original message to confirm the choice locally
            new_embed = interaction.message.embeds[0]
            new_embed.add_field(
                name="Current Orders", value=f"✅ **{clean_choice}**", inline=False
            )
            new_embed.color = discord.Color.green()

            try:
                await interaction.edit_original_response(
                    embed=new_embed, view=disabled_view
                )
                await interaction.followup.send(
                    f"✅ Your orders for **{clean_choice}** have been relayed to the commanders.",
                    ephemeral=True,
                )
            except:
                pass

    async def handle_gate_alert(self, payload: dict):
        """
        Processes a GATE_ALERT event and attaches interactive buttons using Locked Channel IDs.
        """
        guild_id = payload.get("guild_id")
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        defender_info = payload.get("defender", {})
        marcher_info = payload.get("marcher", {})
        attacking_army_id = payload.get("attacking_army_id")

        if not attacking_army_id:
            print("CRITICAL: Gate alert received without an 'attacking_army_id'.")
            return

        target_channel = None
        defender_discord_id = defender_info.get("discord_id")

        # 1. DATABASE LOOKUP (Find Locked Quarters)
        async with get_session() as session:
            game_id = payload.get("game_id")
            if not game_id:
                game = await GameRepo.get_active_game(session, guild_id)
                game_id = game.game_id if game else None

            # Attempt to find the locked channel for the defender
            if not defender_info.get("is_npc") and game_id:
                defender_house_id = defender_info.get("house_id")
                if defender_house_id:
                    target_channel = await self.get_player_channel(
                        session, guild, int(defender_house_id), game_id
                    )

        # 2. DETERMINE TARGET CHANNEL
        # Priority 1: Locked Player Quarters
        # Priority 2: GM Alerts (if NPC or Quarters missing)
        if target_channel:
            final_channel = target_channel
            ping_message = f"<@{defender_discord_id}>" if defender_discord_id else ""
        else:
            final_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
            if defender_info.get("is_npc"):
                ping_message = "🔔 **NPC Gate Alert** (GMs must decide)"
            else:
                ping_message = f"⚠️ **Player channel missing/not locked** for <@{defender_discord_id}>. GM intervention required."

        if not final_channel:
            print(
                f"CRITICAL: Could not find ANY channel to post gate alert for {guild.name}"
            )
            return

        # 3. CONSTRUCT INTERACTIVE ALERT
        embed = discord.Embed(
            title=f"⚔️ Gate Alert: {payload.get('gate_name')}",
            description=f"An army approaches a strategic chokepoint you control!",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Attacking Army",
            value=f"**{marcher_info.get('commander')}** of House **{marcher_info.get('house_name')}**",
            inline=False,
        )
        embed.add_field(
            name="Troop Count",
            value=f"{marcher_info.get('troops', 'N/A')}",
            inline=True,
        )
        embed.set_footer(text="A decision is required: Grant or Deny Passage.")

        # Attach the interactive buttons
        view = GateActionView(
            self.bot, guild.id, attacking_army_id, defender_discord_id
        )

        try:
            await final_channel.send(ping_message, embed=embed, view=view)
            print(f"Successfully sent interactive gate alert to #{final_channel.name}")
        except Exception as e:
            print(f"❌ Error sending gate alert: {e}")

    async def handle_bankruptcy_notification(self, data):
        """
        Alerts GMs about houses running out of gold.
        data = { guild_id, data: [{name, debt, troops}, ...] }
        """
        guild = self.bot.get_guild(data["guild_id"])
        if not guild:
            return

        channel = discord.utils.get(guild.text_channels, name="gm-alerts")
        if not channel:
            return

        embed = discord.Embed(
            title="📉 Logistics Report: Bankruptcy",
            description="The following houses cannot pay their daily army upkeep.",
            color=discord.Color.dark_red(),
        )

        for entry in data["data"]:
            embed.add_field(
                name=f"House {entry['name']}",
                value=f"**Debt:** {entry['debt']} Gold\n**At Risk:** {entry['troops']} Troops",
                inline=False,
            )

        embed.set_footer(text="Use `!punish [House] [Percent]` to simulate desertion.")
        await channel.send(embed=embed)

    async def handle_banner_report(self, data: dict):
        """
        Sends the Banner Report to the Liege's locked private quarters via Channel ID.
        """
        guild = self.bot.get_guild(data.get("guild_id"))
        if not guild:
            return

        liege_house_id = data.get("liege_house_id")
        owner_discord_id = data.get("owner_id")

        target_channel = None

        async with get_session() as session:
            # 1. Resolve Game ID if not provided
            game_id = data.get("game_id")
            if not game_id:
                game = await GameRepo.get_active_game(session, guild.id)
                game_id = game.game_id if game else None

            # 2. Find the Locked Channel using the helper
            if game_id and liege_house_id:
                target_channel = await self.get_player_channel(
                    session, guild, int(liege_house_id), game_id
                )

        # 3. Notification & Fallback Logic
        ping_content = f"<@{owner_discord_id}>" if owner_discord_id else ""

        # Determine the final destination for the report
        if target_channel:
            final_channel = target_channel
        else:
            # Fallback to GM Alerts if player channel is missing/unlocked
            final_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
            if ping_content:
                ping_content = f"⚠️ **Player channel missing/not locked**, sending report here: {ping_content}"

        if not final_channel:
            print(
                f"❌ Critical: Could not find channel to post banner report for House ID {liege_house_id}"
            )
            return

        # 4. Construct the Embed
        embed = discord.Embed(
            title="🦅 Banner Call Report",
            description=f"Summary of the muster for **House {data['liege_house_name']}**.",
            color=discord.Color.blue(),
        )

        report_text = "\n".join(data["report_lines"])
        if len(report_text) > 3000:
            report_text = report_text[:3000] + "...(truncated)"

        embed.add_field(
            name="Vassal Responses",
            value=report_text or "No vassals found.",
            inline=False,
        )

        unit_noun = "ships" if data.get("call_type") == "SEA" else "men"
        embed.add_field(
            name="Total Raised",
            value=f"**{data['total_raised']}** {unit_noun}",
            inline=True,
        )
        embed.add_field(
            name="Full Assembly", value=f"**{data['max_duration']}**", inline=True
        )

        try:
            await final_channel.send(content=ping_content, embed=embed)
        except Exception as e:
            print(f"❌ Failed to send banner report: {e}")

    async def handle_arrival_notification(self, data: dict):
        """
        Sends arrival embeds using Locked Channel IDs, with fallbacks and Fog of War.
        """
        guild_id = data.get("guild_id")
        if not guild_id:
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        unit_noun = "ships" if data.get("unit_type") == "SEA" else "men"
        notification_sent_to_player = False
        owner_discord_id = None
        target_channel = None

        # 1. DATABASE LOOKUP (Find Owner & Locked Channel)
        async with get_session() as session:
            game_id = data.get("game_id")
            if not game_id:
                game = await GameRepo.get_active_game(session, guild_id)
                game_id = game.game_id if game else None

            if game_id:
                # Find the GamePlayer record for this house
                stmt = (
                    select(GamePlayer)
                    .where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.claimed_house_id == int(data["house_id"]),
                    )
                    .options(selectinload(GamePlayer.user))
                )
                player_owner = (await session.execute(stmt)).scalars().first()

                if player_owner:
                    owner_discord_id = player_owner.user.discord_id

                    # PRIORITY: Use the Locked Channel ID
                    if player_owner.private_channel_id:
                        target_channel = self.bot.get_channel(
                            player_owner.private_channel_id
                        )

                    # FALLBACK: Slugify (For legacy players or un-locked channels)
                    if not target_channel:
                        house_slug = slugify(data["house_name"])
                        target_channel = discord.utils.get(
                            guild.text_channels, name=f"{house_slug}-quarters"
                        )

        # 2. PRIVATE NOTIFICATION (Direct to Quarters)
        if owner_discord_id and target_channel:
            embed_private = discord.Embed(
                title="📍 Arrival Report",
                description=f"**{data['commander']}** ({data['troops']} {unit_noun}) has arrived at **{data['location']}**.",
                color=discord.Color.green(),
            )
            try:
                await target_channel.send(embed=embed_private)
                notification_sent_to_player = True
            except:
                pass

        # 3. GM NOTIFICATION (Fallback for NPCs or missing channels)
        if not notification_sent_to_player:
            gm_channel = discord.utils.get(guild.text_channels, name="gm-alerts")
            if gm_channel:
                reason = "NPC/Unclaimed"
                if owner_discord_id and not target_channel:
                    reason = "Channel Missing/Not Locked"

                embed_gm = discord.Embed(
                    title=f"📍 Arrival Report ({reason})",
                    description=(
                        f"**{data['commander']}** of House **{data['house_name']}** "
                        f"({data['troops']} {unit_noun}) has arrived at **{data['location']}**."
                    ),
                    color=discord.Color.blue(),
                )
                await gm_channel.send(embed=embed_gm)

        # 4. PUBLIC NOTIFICATION (Fog of War)
        # We only announce to the public if the troop count hits the threshold
        if int(data.get("troops", 0)) >= FOG_OF_WAR_THRESHOLD:
            # Look for movement reporting channels
            public_channel = discord.utils.get(
                guild.text_channels, name="army-movements"
            ) or discord.utils.get(guild.text_channels, name="general-movements")

            if public_channel:
                # Try to find a role matching the house name for a pretty mention
                house_display = f"**{data['house_name']}**"
                public_msg = (
                    f"✅ The forces of {house_display} under the command of **{data['commander']}** "
                    f"have arrived at **{data['location']}**."
                )
                await public_channel.send(public_msg)

    async def handle_path_notification(self, data):
        guild = self.bot.get_guild(data["guild_id"])
        channel = guild.get_channel(data["channel_id"]) if guild else None
        user = guild.get_member(data["user_id"]) if guild else None

        if not channel or not user:
            return

        if data["type"] == "PATH_FAILED":
            await channel.send(
                f"{user.mention}, your journey plan failed: {data['reason']}"
            )
            return

        try:
            # Need to explicitly check if data['image_path'] is a BytesIO object or a file path string
            # If it's BytesIO, you should pass it directly. If it's a string, ensure os.path.exists
            # The WarfareService._generate_path_image returns BytesIO
            if hasattr(
                data["image"], "read"
            ):  # Check if it's a file-like object (BytesIO)
                image_file = discord.File(data["image"], filename="journey.png")
            elif os.path.exists(data["image_path"]):
                image_file = discord.File(data["image_path"], filename="journey.png")
            else:
                await channel.send(
                    f"{user.mention}, map generated but file not found on server."
                )
                return

            embed = discord.Embed(
                title=f"Journey Plan: {data['origin']} to {data['destination']}",
                description=f"Mode: **{data['mode']}**",
                color=discord.Color.blue(),
            )
            embed.add_field(name="Est. Travel Time", value=data["time"], inline=True)
            embed.add_field(
                name="Distance", value=f"~{data['distance']} miles", inline=True
            )
            embed.set_image(url="attachment://journey.png")

            await channel.send(content=f"{user.mention}", file=image_file, embed=embed)

            # If the image was a temporary file, delete it
            if not hasattr(data["image"], "read") and os.path.exists(
                data["image_path"]
            ):
                os.remove(data["image_path"])

        except Exception as e:
            await channel.send(f"❌ Error sending map: {e}")

    @redis_listener.before_loop
    async def before_listener(self):
        await self.bot.wait_until_ready()

    @commands.command(name="fiefs", aliases=["locations", "places"])
    @commands.check(is_in_house_channel)
    async def list_all_fiefs(self, ctx):
        """Displays a complete, paginated list of all known fiefs."""
        async with ctx.typing():
            async with get_session() as session:
                game = await GameRepo.get_active_game(session, ctx.guild.id)
                if not game:
                    return await ctx.send("❌ No active game.")

                all_fiefs_raw = await FiefRepo.get_all_fief_names(session, game.game_id)
                all_fiefs = sorted(list(set(all_fiefs_raw)))

                if not all_fiefs:
                    return await ctx.send(
                        "❌ No fiefs have been defined for this game."
                    )

                CHUNK_SIZE = 25
                fief_chunks = [
                    all_fiefs[i : i + CHUNK_SIZE]
                    for i in range(0, len(all_fiefs), CHUNK_SIZE)
                ]

                embeds = []
                total_pages = len(fief_chunks)

                for i, chunk in enumerate(fief_chunks):
                    description = "```\n" + "\n".join(chunk) + "\n```"

                    embed = discord.Embed(
                        title="📜 List of Known Fiefs",
                        description=description,
                        color=discord.Color.blurple(),
                    )
                    embed.set_footer(text=f"Page {i + 1} of {total_pages}")
                    embeds.append(embed)

                if not embeds:
                    return await ctx.send("Could not generate the fief list.")

                if len(embeds) == 1:
                    await ctx.send(embed=embeds[0])
                else:
                    view = Paginator(embeds)
                    await ctx.send(embed=embeds[0], view=view)

    @commands.command(name="journey", aliases=["plan"])
    @commands.check(is_in_house_channel)
    async def journey(self, ctx):
        """Initiates the interactive journey planning UI for any army or fleet."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )
            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not have a house to command.")

            available_units = (
                (
                    await session.execute(
                        select(Army).where(Army.house_id == player.claimed_house_id)
                    )
                )
                .scalars()
                .all()
            )

            if not available_units:
                return await ctx.send(
                    "You have no units to use as a starting point for a plan."
                )

            view = JourneyArmySelectView(bot=self.bot, armies=available_units)
            await ctx.send(
                "**Step 1: Select a starting unit for your journey plan.**",
                view=view,
                ephemeral=True,
            )

    # --- PLAYER COMMANDS ---
    @commands.command(name="march")
    @commands.check(is_in_house_channel)
    async def march(self, ctx, army_id: int = None):
        """
        Initiates march orders for LAND armies.
        Usage:
        !march          (Select from menu)
        !march [ID]     (Shortcut to specific army)
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # Get Player
            player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )
            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not have a house to command.")

            # --- PATH A: SHORTCUT (!march 123) ---
            if army_id:
                army = await ArmyRepo.get_army_by_id(session, army_id)

                # Validation
                if not army:
                    return await ctx.send(f"❌ Army ID {army_id} not found.")
                if army.house_id != player.claimed_house_id:
                    # Optional: Check for Liege Lord authority here if desired
                    return await ctx.send(f"❌ You do not command Army {army_id}.")
                if army.army_type != "LAND":
                    return await ctx.send(f"❌ Unit {army_id} is a fleet. Use `!sail`.")
                if army.status not in ["IDLE", "GARRISONED", "RETREATING"]:
                    return await ctx.send(
                        f"❌ Army is currently **{army.status}** and cannot receive new orders."
                    )

                # Create Shortcut View
                from app.ui.march_view import DirectMarchView

                view = DirectMarchView(self.bot, army)

                return await ctx.send(
                    f"👣 **Orders for {army.commander_name}** ({army.troop_count} men)\n"
                    f"Click below to set destination.",
                    view=view,
                )

            # --- PATH B: MENU SELECTION (!march) ---
            available_armies = (
                (
                    await session.execute(
                        select(Army).where(
                            Army.house_id == player.claimed_house_id,
                            Army.army_type == "LAND",
                            Army.status.in_(["IDLE", "GARRISONED", "RETREATING"]),
                        )
                    )
                )
                .scalars()
                .all()
            )

            if not available_armies:
                return await ctx.send(
                    "You have no idle land armies available to march."
                )

            # Import locally to avoid circulars if necessary, or ensure top-level import
            from app.ui.march_view import ArmySelectView

            view = ArmySelectView(bot=self.bot, armies=available_armies)

            await ctx.send(
                "**Step 1: Select an army to move.**",
                view=view,
                ephemeral=True,
            )

    @commands.command(name="redirect")
    @commands.check(is_in_house_channel)
    async def redirect(self, ctx):
        """Initiates the interactive UI to redirect a moving army or fleet."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game found.")

            player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )

            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not have a house to command.")

            moving_units = (
                (
                    await session.execute(
                        select(Army).where(
                            Army.house_id == player.claimed_house_id,
                            Army.status.in_(["MARCHING", "SAILING"]),
                        )
                    )
                )
                .scalars()
                .all()
            )

            if not moving_units:
                return await ctx.send("You have no moving units to redirect.")

            now = datetime.datetime.now(datetime.timezone.utc)
            valid_units = []

            for unit in moving_units:
                dep_time = unit.departure_time
                if dep_time and dep_time.tzinfo is None:
                    dep_time = dep_time.replace(tzinfo=datetime.timezone.utc)

                if dep_time and dep_time > now:
                    continue

                valid_units.append(unit)

            if not valid_units:
                return await ctx.send(
                    "You have no active moving units to redirect (units inside moving fleets cannot be redirected individually)."
                )

            view = RedirectSelectView(bot=self.bot, armies=valid_units)
            await ctx.send(
                "**Step 1: Select a unit to redirect.**",
                view=view,
                ephemeral=True,
            )

    @commands.command(name="army")
    @commands.check(is_in_house_channel)
    async def army_details(self, ctx):
        """
        Detailed military report, paginated for large forces.
        """
        discord_id = ctx.author.id
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == discord_id, GamePlayer.game_id == game.game_id
                )
                .options(selectinload(GamePlayer.house))
            )
            player = (await session.execute(stmt)).scalars().first()

            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not have a house.")

            stmt_a = (
                select(Army)
                .where(
                    Army.house_id == player.claimed_house_id,
                    Army.status != "EMBARKED",
                )
                .order_by(Army.status.desc(), Army.troop_count.desc())
            )
            armies = (await session.execute(stmt_a)).scalars().all()

            if not armies:
                return await ctx.send("You have no military forces.")

            army_chunks = [armies[i : i + 10] for i in range(0, len(armies), 10)]
            embeds = []

            now = datetime.datetime.now(datetime.timezone.utc)

            for chunk in army_chunks:
                embed = discord.Embed(
                    title=f"⚔️ Military Report: House {player.house.name}",
                    color=discord.Color.red(),
                )

                for army in chunk:
                    if army.departure_time:
                        dep_time = army.departure_time
                        if dep_time.tzinfo is None:
                            dep_time = dep_time.replace(tzinfo=datetime.timezone.utc)

                        if dep_time > now:
                            continue

                    if army.status in ["MARCHING", "SAILING"]:
                        if army.arrival_time:
                            arr_time = army.arrival_time
                            if arr_time.tzinfo is None:
                                arr_time = arr_time.replace(
                                    tzinfo=datetime.timezone.utc
                                )

                            if arr_time > now:
                                remaining = arr_time - now
                                hours, rem = divmod(
                                    int(remaining.total_seconds()), 3600
                                )
                                minutes, _ = divmod(rem, 60)
                                time_str = f"{hours}h {minutes}m"
                            else:
                                time_str = "Arriving..."
                        else:
                            time_str = "???"

                        status_icon = "🦶" if army.status == "MARCHING" else "⛵"
                        status_str = f"{status_icon} {army.status} (ETA: {time_str})"
                    else:
                        status_str = f"🟢 {army.status}"

                    comp_items = []
                    if army.composition:
                        for k, v in army.composition.items():
                            if v <= 0:
                                continue
                            if army.army_type == "SEA" and k.lower() in [
                                "ships",
                                "ship",
                                "galley",
                                "galleys",
                            ]:
                                continue
                            comp_items.append(f"{k.title()[:3]}: {v}")

                    comp_str = " | ".join(comp_items)
                    if not comp_str:
                        comp_str = "-"

                    loc_str = f"{army.location_x:.0f}, {army.location_y:.0f}"

                    cargo_str = ""
                    if army.army_type == "SEA":
                        count_label = "Ships"
                        cargo_data = {}
                        if army.cargo:
                            if isinstance(army.cargo, dict):
                                cargo_data = army.cargo
                            elif isinstance(army.cargo, str):
                                try:
                                    cargo_data = json.loads(army.cargo)
                                except:
                                    pass

                        if cargo_data.get("troop_count", 0) > 0:
                            cargo_str = (
                                f"\n📦 **Cargo:** {cargo_data['troop_count']} men"
                            )
                    else:
                        count_label = "Troops"

                    embed.add_field(
                        name=f"{army.commander_name} (ID: {army.army_id})",
                        value=f"**Status:** {status_str}\n**{count_label}:** {army.troop_count}{cargo_str}\n**Comp:** {comp_str}\n**Location:** {loc_str}",
                        inline=False,
                    )

                embeds.append(embed)

            if embeds:
                if len(embeds) == 1:
                    await ctx.send(embed=embeds[0])
                else:
                    view = Paginator(embeds)
                    await ctx.send(embed=embeds[0], view=view)

    @commands.command(name="split")
    @commands.check(is_in_house_channel)
    async def split(self, ctx, army_id: int, amount: int, *, new_name: str):
        """Splits an army. Usage: !split [ID] [Amount] [Name]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()

            service = WarfareService(session)
            success, msg = await service.split_army(
                game.game_id, user.user_id, army_id, amount, new_name
            )
            await ctx.send(msg)

    @commands.command(name="merge")
    @commands.check(is_in_house_channel)
    async def merge(self, ctx, army_id_1: int, army_id_2: int):
        """Merges two armies. Usage: !merge [ID1] [ID2]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()

            service = WarfareService(session)
            success, msg = await service.merge_armies(
                game.game_id, user.user_id, army_id_1, army_id_2
            )
            await ctx.send(msg)

    @commands.command(name="form_coalition")
    async def form_coalition(self, ctx, new_name: str, *army_ids: int):
        """
        Merges multiple armies.
        - If you own all armies, they merge instantly.
        - If armies are owned by multiple players, a consent proposal is created.
        Usage: !form_coalition "Name" 101 102 103
        """
        if not army_ids:
            return await ctx.send("❌ You must provide at least two army IDs.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game found.")

            armies = await ArmyRepo.get_armies_by_ids(session, list(army_ids))
            if len(armies) != len(army_ids):
                return await ctx.send("❌ One or more army IDs are invalid.")

            owners_map = {}
            for army in armies:
                player_owner = await session.scalar(
                    select(GamePlayer)
                    .join(User)
                    .where(
                        GamePlayer.claimed_house_id == army.house_id,
                        GamePlayer.is_primary == True,
                    )
                    .options(selectinload(GamePlayer.user))
                )
                if not player_owner or not player_owner.user:
                    return await ctx.send(
                        f"❌ Could not find a player for House {army.house.name}."
                    )

                owner_id = player_owner.user.discord_id
                if owner_id not in owners_map:
                    owners_map[owner_id] = []
                owners_map[owner_id].append(army)

            if len(owners_map) == 1 and ctx.author.id in owners_map:
                await ctx.send("🤝 Merging your own units...")
                service = WarfareService(session)
                success, msg = await service.form_coalition(
                    game.game_id, ctx.author.id, new_name, army_ids
                )
                await ctx.send(msg)

            else:
                targets_map = {}
                mentions = []
                for owner_id, owned_armies in owners_map.items():
                    try:
                        member = await ctx.guild.fetch_member(owner_id)
                    except discord.NotFound:
                        return await ctx.send(
                            f"❌ Player with ID {owner_id} not found in this server."
                        )
                    except discord.HTTPException:
                        return await ctx.send(
                            f"❌ Discord API error while fetching user {owner_id}."
                        )

                    targets_map[member] = owned_armies

                    if member != ctx.author:
                        mentions.append(member.mention)

                view = CoalitionConsentView(
                    self.bot, ctx.author, targets_map, game.game_id, new_name, army_ids
                )
                await ctx.send(
                    f"A coalition has been proposed! {', '.join(mentions)}",
                    embed=view.create_embed(),
                    view=view,
                )

    @commands.command(name="disband")
    @commands.check(is_in_house_channel)
    async def disband_coalition(self, ctx, army_id: int):
        """Disbands a coalition. Usage: !disband [ID]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()

            service = WarfareService(session)
            success, msg = await service.disband_coalition(
                game.game_id, user.user_id, army_id
            )
            await ctx.send(msg)

    # --- ADMIN/GM COMMANDS ---
    @commands.command(name="rush")
    @commands.has_permissions(
        administrator=True
    )  # Existing admin check, effectively GM
    async def admin_rush(self, ctx, army_id: int):
        """GM Tool: Force an army to arrive by running its task now."""
        resolve_army_arrival.delay(army_id)
        await ctx.send(
            f"⚡ **Divine Wind:** Arrival task for Army {army_id} sent to worker immediately."
        )

    @commands.command(name="rush_all")
    @commands.has_permissions(
        administrator=True
    )  # Existing admin check, effectively GM
    async def rush_all(self, ctx, *, destination: str = None):
        """
        GM Tool: Instantly completes marches.
        Usage: !rush_all Winterfell (Rushes everyone marching to Winterfell)
        Usage: !rush_all (Rushes EVERY marching army in the game)
        """
        count = 0
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = select(Army).where(
                Army.game_id == game.game_id, Army.status.in_(["MARCHING", "SAILING"])
            )

            if destination:
                service = WarfareService(session)
                loc = await service._get_location_from_db(game.game_id, destination)
                if not loc:
                    await ctx.send(f"❌ Location **{destination}** not found.")
                    return

                stmt = stmt.where(
                    Army.destination_x == loc["x"], Army.destination_y == loc["y"]
                )

            armies = (await session.execute(stmt)).scalars().all()

            if not armies:
                await ctx.send("⚠️ No armies found matching those criteria.")
                return

            for army in armies:
                resolve_army_arrival.delay(army.army_id)
                count += 1

            target_msg = (
                f"to **{destination}**" if destination else "in the **entire world**"
            )
            await ctx.send(
                f"⚡ **Divine Wind:** Rushed **{count}** armies {target_msg}."
            )

    @commands.group(name="worldrule", invoke_without_command=True)
    @commands.has_permissions(
        administrator=True
    )  # Existing admin check, effectively GM
    async def worldrule(self, ctx):
        """Parent command for managing world rules."""
        await ctx.send("Subcommands: `setbridge`, `setrivers`, `setsea`.")

    @worldrule.command(name="setbridge")
    @commands.has_permissions(administrator=True)
    async def set_bridge(self, ctx, bridge_name: str, status: str):
        bridge_map = {
            "twins": "twins_open",
            "rubyford": "rubyford_open",
            "bitterbridge": "bitterbridge_open",
        }
        rule_name = bridge_map.get(bridge_name.lower())
        if not rule_name:
            return await ctx.send("❌ Invalid bridge name.")
        is_enabled = status.lower() in ["on", "open", "enabled"]
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return
            msg = await WarfareService(session).set_world_rule(
                game.game_id, rule_name, is_enabled
            )
            await ctx.send(msg)

    @worldrule.command(name="setrivers")
    @commands.has_permissions(administrator=True)
    async def set_rivers(self, ctx, status: str):
        is_enabled = status.lower() in ["impassable", "on", "enabled"]
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return
            msg = await WarfareService(session).set_world_rule(
                game.game_id, "rivers_impassable", is_enabled
            )
            await ctx.send(msg)

    @worldrule.command(name="setsea")
    @commands.has_permissions(administrator=True)
    async def set_sea(self, ctx, status: str):
        is_enabled = status.lower() in ["allowed", "on", "enabled"]
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return
            msg = await WarfareService(session).set_world_rule(
                game.game_id, "sea_travel_allowed", is_enabled
            )
            await ctx.send(msg)

    @commands.command(name="sail")
    @commands.check(is_in_house_channel)
    async def sail(self, ctx, fleet_id: int = None):
        """
        Initiates sail orders.
        Usage:
        !sail          (Select from menu)
        !sail [ID]     (Shortcut to specific fleet)
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # Get Player
            player = await session.scalar(
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )
            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not have a house to command.")

            # --- PATH A: SHORTCUT (!sail 123) ---
            if fleet_id:
                fleet = await ArmyRepo.get_army_by_id(session, fleet_id)

                # Validation
                if not fleet:
                    return await ctx.send(f"❌ Fleet ID {fleet_id} not found.")
                if fleet.house_id != player.claimed_house_id:
                    return await ctx.send(f"❌ You do not command Fleet {fleet_id}.")
                if fleet.army_type != "SEA":
                    return await ctx.send(
                        f"❌ Unit {fleet_id} is not a fleet. Use `!march`."
                    )
                if fleet.status not in ["IDLE", "DOCKED", "GARRISONED", "RETREATING"]:
                    return await ctx.send(
                        f"❌ Fleet is currently **{fleet.status}** and cannot receive new orders."
                    )

                # Create Shortcut View
                # Hack: attach discord ID to fleet obj temporarily for the view check
                fleet.player_discord_id = ctx.author.id

                view = DirectSailView(self.bot, fleet, game.ship_capacity)
                return await ctx.send(
                    f"⚓ **Orders for {fleet.commander_name}** ({fleet.troop_count} ships)\n"
                    f"Click below to set destination and cargo.",
                    view=view,
                )

            # --- PATH B: MENU SELECTION (!sail) ---
            # Fetch only the user's available SEA armies (fleets)
            available_fleets = (
                (
                    await session.execute(
                        select(Army).where(
                            Army.house_id == player.claimed_house_id,
                            Army.army_type == "SEA",
                            Army.status.in_(
                                ["IDLE", "DOCKED", "GARRISONED", "RETREATING"]
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )

            if not available_fleets:
                return await ctx.send("You have no fleets available to sail.")

            view = FleetSelectView(bot=self.bot, fleets=available_fleets)
            await ctx.send(
                "**Step 1: Select a fleet to command.**",
                view=view,
            )

    @commands.command(name="stop", aliases=["halt"])
    @commands.check(is_in_house_channel)
    async def stop(self, ctx, army_id: int):
        """Stops a moving army or fleet immediately. Usage: !stop [ID]"""
        async with get_session() as session:
            # 1. Setup Game & User
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return await ctx.send("❌ You are not registered.")

            # 2. Call Service
            service = WarfareService(session)
            success, msg = await service.stop_march(game.game_id, user.user_id, army_id)

            # 3. Handle "Ghost Army" Cleanup (The Fleet Patch)
            # If we successfully stopped a FLEET, we must check if there was a
            # pre-scheduled Land Army waiting for it (Ghost Army) and delete it.
            if success:
                # We re-fetch the army to check if it was a Fleet
                army = await ArmyRepo.get_army_by_id(session, army_id)
                if army and army.army_type == "SEA":
                    # Look for any future marches for this house
                    import datetime

                    now = datetime.datetime.now(datetime.timezone.utc)
                    stmt_ghost = select(Army).where(
                        Army.house_id == army.house_id,
                        Army.army_type == "LAND",
                        Army.status == "MARCHING",
                        Army.departure_time > now,
                    )
                    ghosts = (await session.execute(stmt_ghost)).scalars().all()

                    if ghosts:
                        for ghost in ghosts:
                            # Put troops back into the fleet cargo
                            if not army.cargo:
                                army.cargo = {
                                    "commander": ghost.commander_name,
                                    "troop_count": ghost.troop_count,
                                    "composition": ghost.composition,
                                }
                            # Delete the ghost
                            await session.delete(ghost)

                        await session.commit()
                        msg += "\n(⚠️ Cancelled scheduled disembarkation orders)"

            await ctx.send(msg)

    @commands.command(name="embark")
    @commands.check(is_in_house_channel)
    async def embark(self, ctx, land_army_id: int, fleet_id: int):
        """
        Loads a land army onto a fleet at the same location.
        Usage: !embark [Land_Army_ID] [Fleet_ID]
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return

            service = WarfareService(session)
            success, msg = await service.embark_army(
                game.game_id, user.user_id, land_army_id, fleet_id
            )
            await ctx.send(msg)

    @commands.command(name="disembark")
    @commands.check(is_in_house_channel)
    async def disembark(self, ctx, army_id: int):
        """
        Unloads troops from a fleet to the current location.
        Usage: !disembark [Fleet_ID]
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return

            service = WarfareService(session)
            success, msg = await service.disembark_army(
                game.game_id, user.user_id, army_id
            )
            await ctx.send(msg)

    @commands.command(name="recruit")
    @commands.check(
        recruitment_is_enabled
    )  # This now correctly checks the manpower_enabled flag
    @commands.check(is_in_house_channel)
    async def recruit(self, ctx, fief_name: str, amount: int):
        """
        Recruit troops from your manpower pool into a garrison.
        Usage: !recruit Winterfell 1000
        """
        # NO CHANGES NEEDED HERE. The decorators and service handle everything.
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            stmt = select(User).where(User.discord_id == ctx.author.id)
            user = (await session.execute(stmt)).scalars().first()
            if not user:
                return

            service = WarfareService(session)
            success, msg = await service.recruit_troops(
                game.game_id, user.user_id, fief_name, amount
            )
            await ctx.send(msg)

    # --- GM Warfare Group Commands ---
    @commands.group(name="gm_war", invoke_without_command=True)
    @commands.check(is_gm)
    async def gm_war(self, ctx):
        """GM commands for warfare and army management for NPCs."""
        await ctx.send(
            "GM Warfare Subcommands: `march`, `sail`, `stop`, `split`, `merge`, `form_coalition`, `disband_coalition`, `embark`, `disembark`, `recruit`, `occupy`, `redirect`."
        )

    @gm_war.command(name="march")
    @commands.check(is_gm)
    async def gm_march(
        self,
        ctx,
        target_house_id: int,
        army_id: int = None,
        dest_name: str = None,
        units_input: str = "all",
        commander: str = None,
        gold_to_carry: int = 0,
        *,
        waypoints: str = None,
    ):
        """
        GM: March an NPC army.
        1. Select:   !gm_war march [HouseID]
        2. Shortcut: !gm_war march [HouseID] [ArmyID]
        3. Manual:   !gm_war march [HouseID] [ArmyID] [Destination] ...
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # --- CASE 1: INTERACTIVE MENU (No Army ID) ---
            if army_id is None:
                armies = (
                    (
                        await session.execute(
                            select(Army).where(
                                Army.house_id == target_house_id,
                                Army.status.in_(["IDLE", "GARRISONED", "RETREATING"]),
                                Army.army_type == "LAND",  # Only show land armies
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

                if not armies:
                    return await ctx.send(
                        f"❌ House {target_house_id} has no idle land armies."
                    )

                from app.ui.gm_march_view import GMMarchArmySelectView

                view = GMMarchArmySelectView(self.bot, armies, target_house_id)
                await ctx.send(
                    f"👑 **GM Command:** Commanding House {target_house_id}", view=view
                )
                return

            # --- CASE 2: SHORTCUT UI (Army ID, but NO Destination) ---
            if army_id is not None and dest_name is None:
                army = await ArmyRepo.get_army_by_id(session, army_id)

                # Validation
                if not army:
                    return await ctx.send(f"❌ Army {army_id} not found.")
                if army.house_id != target_house_id:
                    return await ctx.send(
                        f"❌ Army {army_id} does not belong to House {target_house_id}."
                    )
                if army.army_type != "LAND":
                    return await ctx.send("❌ Unit is a fleet. Use `!gm_war sail`.")

                # Import new view
                from app.ui.gm_march_view import DirectGMMarchView

                view = DirectGMMarchView(self.bot, army, target_house_id)
                await ctx.send(
                    f"👣 **GM Override:** Orders for {army.commander_name} (ID: {army.army_id})\n"
                    f"Click below to set destination.",
                    view=view,
                )
                return

            # --- CASE 3: MANUAL EXECUTION (All Args Provided) ---
            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found.")

            service = WarfareService(session)
            success, result_or_msg, fog_msg = await service.march_army(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                identifier=str(army_id),
                dest_name=dest_name,
                units_input=units_input,
                commander=commander,
                gold_to_carry=gold_to_carry,
                waypoints=waypoints,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )

            if success:
                # 1. GM Feedback
                response_embed = discord.Embed(
                    title=f"✅ GM March Order: House {target_house_id}",
                    description=f"**{result_or_msg['commander']}** ({result_or_msg['count']} men) -> **{result_or_msg['destination']}**.",
                    color=discord.Color.green(),
                )
                response_embed.add_field(
                    name="Est. Time", value=result_or_msg["time"], inline=True
                )
                response_embed.add_field(
                    name="Gold",
                    value=str(result_or_msg.get("gold_carried", 0)),
                    inline=True,
                )

                if result_or_msg.get("image"):
                    image_file = discord.File(
                        result_or_msg["image"], filename="journey_gm.png"
                    )
                    response_embed.set_image(url="attachment://journey_gm.png")
                    await ctx.send(file=image_file, embed=response_embed)
                    result_or_msg["image"].close()
                else:
                    await ctx.send(embed=response_embed)

                # 2. Public Fog of War
                if fog_msg:
                    gen_channel = discord.utils.get(
                        ctx.guild.text_channels, name="general-movements"
                    )
                    if gen_channel:
                        await gen_channel.send(fog_msg)
            else:
                await ctx.send(f"❌ GM Command Failed: {result_or_msg}")

    @gm_war.command(name="sail")
    @commands.check(is_gm)
    async def gm_sail(
        self,
        ctx,
        target_house_id: int,
        fleet_id: int = None,
        dest_name: str = None,
        ships_input: str = "all",
        units_input: str = None,
        commander: str = None,
        gold_to_carry: int = 0,
        *,
        waypoints: str = None,
    ):
        """
        GM: Sail an NPC fleet.
        1. Select:   !gm_war sail [HouseID]
        2. Shortcut: !gm_war sail [HouseID] [FleetID]
        3. Manual:   !gm_war sail [HouseID] [FleetID] [Dest] ...
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # --- CASE 1: INTERACTIVE MENU (No Fleet ID) ---
            if fleet_id is None:
                # Fetch available fleets for this NPC house
                fleets = (
                    (
                        await session.execute(
                            select(Army).where(
                                Army.house_id == target_house_id,
                                Army.army_type == "SEA",
                                Army.status.in_(
                                    ["IDLE", "DOCKED", "GARRISONED", "RETREATING"]
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

                if not fleets:
                    return await ctx.send(
                        f"❌ House {target_house_id} has no available fleets."
                    )

                from app.ui.gm_sail_view import GMFleetSelectView

                view = GMFleetSelectView(self.bot, fleets, target_house_id)
                await ctx.send(
                    f"👑 **GM Command:** Admiral for House {target_house_id}", view=view
                )
                return

            # --- CASE 2: SHORTCUT UI (Fleet ID, but NO Destination) ---
            if fleet_id is not None and dest_name is None:
                fleet = await ArmyRepo.get_army_by_id(session, fleet_id)

                if not fleet:
                    return await ctx.send(f"❌ Fleet {fleet_id} not found.")
                if fleet.house_id != target_house_id:
                    return await ctx.send(
                        f"❌ Fleet {fleet_id} does not belong to House {target_house_id}."
                    )
                if fleet.army_type != "SEA":
                    return await ctx.send("❌ Unit is not a fleet.")

                # Import the new Direct View
                from app.ui.gm_sail_view import DirectGMSailView

                view = DirectGMSailView(
                    self.bot, fleet, target_house_id, game.ship_capacity
                )
                await ctx.send(
                    f"⚓ **GM Override:** Orders for {fleet.commander_name} (ID: {fleet.army_id})\n"
                    f"Click below to set destination and cargo.",
                    view=view,
                )
                return

            # --- CASE 3: MANUAL EXECUTION (All Args Provided) ---
            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found.")

            service = WarfareService(session)
            success, result_or_msg, fog_msg = await service.sail_fleet(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                fleet_id=fleet_id,
                ships_input=ships_input,
                dest_name=dest_name,
                units_input=units_input,
                commander=commander,
                gold_to_carry=gold_to_carry,
                waypoints=waypoints,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )

            if success:
                # GM Feedback
                response_embed = discord.Embed(
                    title=f"✅ GM Sail Order: House {target_house_id}",
                    description=f"**{result_or_msg['commander']}** ({result_or_msg['count']} men) {result_or_msg.get('journey_summary', 'set sail')}.",
                    color=discord.Color.green(),
                )
                response_embed.add_field(
                    name="Est. Time", value=result_or_msg["time"], inline=True
                )

                if result_or_msg.get("image"):
                    image_file = discord.File(
                        result_or_msg["image"], filename="journey_gm.png"
                    )
                    response_embed.set_image(url="attachment://journey_gm.png")
                    await ctx.send(file=image_file, embed=response_embed)
                    result_or_msg["image"].close()
                else:
                    await ctx.send(embed=response_embed)

                # Public Fog
                if fog_msg:
                    gen_channel = discord.utils.get(
                        ctx.guild.text_channels, name="general-movements"
                    )
                    if gen_channel:
                        await gen_channel.send(fog_msg)
            else:
                await ctx.send(f"❌ GM Command Failed: {result_or_msg}")

    @gm_war.command(name="stop")
    @commands.check(is_gm)
    async def gm_stop(self, ctx, target_house_id: int, army_id: int):
        """GM: Stop an NPC house's moving army/fleet. Usage: !gm_war stop [HouseID] [ArmyID]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.stop_march(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=army_id,
                is_admin=True,  # Use this to ensure it stops regardless of claimed_house_id
                is_gm_override=True,  # For consistency in signaling GM intent
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="split")
    @commands.check(is_gm)
    async def gm_split(
        self, ctx, target_house_id: int, army_id: int, amount: int, *, new_name: str
    ):
        """GM: Split an NPC house's army. Usage: !gm_war split [HouseID] [ArmyID] [Amount] [NewName]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.split_army(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=army_id,
                split_amount=amount,
                new_name=new_name,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="merge")
    @commands.check(is_gm)
    async def gm_merge(self, ctx, target_house_id: int, army_id_1: int, army_id_2: int):
        """GM: Merge two of an NPC house's armies. Usage: !gm_war merge [HouseID] [ArmyID1] [ArmyID2]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.merge_armies(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                id_1=army_id_1,
                id_2=army_id_2,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="form_coalition")
    @commands.check(is_gm)
    async def gm_form_coalition(
        self, ctx, leader_house_id: int, new_name: str, *army_ids: int
    ):
        """
        GM: Form a coalition from ANY armies.
        leader_house_id: The House that will control the new Coalition.
        army_ids: List of armies to merge (can belong to different houses).
        Usage: !gm_war form_coalition [LeaderHouseID] "Grand Host" 101 102 103
        """
        if len(army_ids) < 2:
            return await ctx.send("❌ You must provide at least two army IDs.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game found.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)

            success, msg = await service.form_coalition(
                game_id=game.game_id,
                leader_user_id=gm_user_obj.user_id,
                new_name=new_name,
                army_ids=army_ids,
                bypass_auth=True,  # Bypasses "User owns Army" check
                is_gm_override=True,  # Bypasses "Army matches House" check
                acting_house_id=leader_house_id,  # The resulting owner
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="disband_coalition")
    @commands.check(is_gm)
    async def gm_disband_coalition(self, ctx, target_house_id: int, army_id: int):
        """GM: Disband a coalition belonging to an NPC house. Usage: !gm_war disband_coalition [HouseID] [CoalitionArmyID]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.disband_coalition(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=army_id,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="embark")
    @commands.check(is_gm)
    async def gm_embark(
        self, ctx, target_house_id: int, land_army_id: int, fleet_id: int
    ):
        """GM: Embark an NPC land army onto an NPC fleet. Usage: !gm_war embark [HouseID] [LandArmyID] [FleetID]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.embark_army(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                land_army_id=land_army_id,
                fleet_id=fleet_id,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="disembark")
    @commands.check(is_gm)
    async def gm_disembark(self, ctx, target_house_id: int, fleet_id: int):
        """GM: Disembark troops from an NPC fleet. Usage: !gm_war disembark [HouseID] [FleetID]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.disembark_army(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=fleet_id,  # The army_id parameter in disembark_army is actually the fleet_id
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @gm_war.command(name="recruit")
    @commands.check(is_gm)
    async def gm_recruit(self, ctx, target_house_id: int, fief_name: str, amount: int):
        """GM: Recruit troops for an NPC house. Usage: !gm_war recruit [HouseID] [FiefName] [Amount]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.recruit_troops(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                fief_name=fief_name,
                amount=amount,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

    @commands.command()
    async def occupy(self, ctx, army_id: int):
        """Occupies a Fief if it is undefended."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                await ctx.send("❌ No active game found in this server.")
                return

            service = WarfareService(session)
            success, msg = await service.occupy_fief(
                game.game_id, ctx.author.id, army_id
            )

            await ctx.send(msg)

            # --- ADDED: Public Event Notification ---
            if success:
                news_chan = discord.utils.get(
                    ctx.guild.text_channels, name="news-and-events"
                )
                if news_chan:
                    embed = discord.Embed(
                        title="🏰 Fief Occupied!",
                        description=msg,
                        color=discord.Color.gold(),
                    )
                    await news_chan.send(embed=embed)

    @gm_war.command(name="occupy")
    @commands.check(is_gm)
    async def gm_occupy(self, ctx, target_house_id: int, army_id: int):
        """GM: Make an NPC army occupy an undefended fief."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, msg = await service.occupy_fief(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=army_id,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            await ctx.send(f"✅ GM Command: {msg}")

            # --- ADDED: Public Event Notification ---
            if success:
                news_chan = discord.utils.get(
                    ctx.guild.text_channels, name="news-and-events"
                )
                if news_chan:
                    embed = discord.Embed(
                        title="🏰 Fief Occupied (GM Event)",
                        description=msg,
                        color=discord.Color.gold(),
                    )
                    await news_chan.send(embed=embed)

    @gm_war.command(name="redirect")
    @commands.check(is_gm)
    async def gm_redirect(
        self,
        ctx,
        target_house_id: int,
        army_id: int,
        new_dest_name: str,
        *,
        new_waypoints: str = None,
    ):
        """GM: Redirect a moving NPC army/fleet. Usage: !gm_war redirect [HouseID] [ArmyID] [NewDestination] [NewWaypoints]"""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            gm_user_obj = await session.scalar(
                select(User).where(User.discord_id == ctx.author.id)
            )
            if not gm_user_obj:
                return await ctx.send("❌ GM user not found in DB.")

            service = WarfareService(session)
            success, result_or_msg, fog_msg = await service.redirect_army(
                game_id=game.game_id,
                user_id=gm_user_obj.user_id,
                army_id=army_id,
                new_dest_name=new_dest_name,
                new_waypoints=new_waypoints,
                is_gm_override=True,
                acting_house_id=target_house_id,
            )
            if success:
                response_embed = discord.Embed(
                    title=f"✅ GM Command: NPC Redirect Order for House ID {target_house_id}",
                    description=f"**{result_or_msg.get('commander', 'Unit')}** ({result_or_msg.get('count', 'N/A')} troops) redirected to **{result_or_msg.get('destination', 'N/A')}**.",
                    color=discord.Color.green(),
                )
                response_embed.add_field(
                    name="Est. Time",
                    value=result_or_msg.get("time", "N/A"),
                    inline=True,
                )
                if result_or_msg.get("image"):
                    image_file = discord.File(
                        result_or_msg["image"], filename="redirect_gm.png"
                    )
                    response_embed.set_image(url="attachment://redirect_gm.png")
                    await ctx.send(file=image_file, embed=response_embed)
                    result_or_msg["image"].close()
                else:
                    await ctx.send(embed=response_embed)

                if fog_msg:
                    gm_channel = discord.utils.get(
                        ctx.guild.text_channels, name="gm-alerts"
                    )
                    if gm_channel:
                        await gm_channel.send(
                            f"🌐 **FOW Report for GM (House ID {target_house_id}):** {fog_msg}"
                        )
            else:
                await ctx.send(f"❌ GM Command Failed: {result_or_msg}")

    @gm_war.command(name="delete_army")
    @commands.check(is_gm)
    async def gm_delete_army(self, ctx, army_id: int):
        """
        GM: Force delete an army (and its cargo).
        Usage: !gm_war delete_army [ArmyID]
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = WarfareService(session)
            success, msg = await service.delete_army(game.game_id, army_id)

            await ctx.send(msg)

    @gm_war.command(name="transfer")
    @commands.check(is_gm)
    async def gm_transfer(self, ctx, source_army_id: int, target_army_id: int):
        """
        GM: Instantly transfers one army's troops into another, deleting the source.
        Bypasses all game rules (owner, status, location).
        Usage: !gm_war transfer [SourceArmyID] [TargetArmyID]
        """
        async with get_session() as session:
            # We don't need game or user objects here as the service will handle validation

            service = WarfareService(session)
            success, msg = await service.gm_transfer_army(
                source_army_id=source_army_id,
                target_army_id=target_army_id,
            )

            if success:
                await ctx.send(f"✅ **GM Transfer Complete:** {msg}")
            else:
                await ctx.send(f"❌ **GM Transfer Failed:** {msg}")

    @gm_war.command(name="reassign")
    @commands.check(is_gm)
    async def gm_reassign(self, ctx, army_id: int, target_house_id: int):
        """
        GM: Instantly reassigns an army to a new house without changing its status.
        Usage: !gm_war reassign [ArmyID] [NewOwnerHouseID]
        """
        async with get_session() as session:
            service = WarfareService(session)
            success, msg = await service.gm_reassign_army(
                army_id=army_id,
                new_owner_house_id=target_house_id,
            )

            if success:
                await ctx.send(f"✅ **GM Reassignment Complete:** {msg}")
            else:
                await ctx.send(f"❌ **GM Reassignment Failed:** {msg}")

    @gm_war.command(name="plan")
    @commands.check(is_gm)
    async def gm_plan(
        self,
        ctx,
        army_id: int,
        destination: str,
        units: str = "all",
        mode: str = "optimal",
        *,
        waypoints: str = None,
    ):
        """
        GM: Simulate a journey to see the path/map without moving.
        Usage: !gm_war plan [ArmyID] [Destination] [Units] [Mode] [Waypoints]
        Example: !gm_war plan 1886 "King's Landing" all optimal
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            service = WarfareService(session)

            # Call the existing planning logic
            success, result = await service.plan_journey(
                game_id=game.game_id,
                source_army_id=army_id,
                dest_name=destination,
                units_input=units,
                travel_mode_req=mode.lower(),  # 'land_only', 'sea_only', or 'optimal'
                waypoints=waypoints,
            )

            if success:
                embed = discord.Embed(
                    title=f"🗺️ Journey Plan: {result['origin']} ➜ {result['destination']}",
                    description=f"**Mode:** {result['mode'].title()}",
                    color=discord.Color.blue(),
                )
                embed.add_field(name="Est. Time", value=result["time"], inline=True)
                embed.add_field(
                    name="Distance", value=f"~{result['distance']} miles", inline=True
                )
                embed.add_field(
                    name="Army Size", value=f"{result['army_size']} troops", inline=True
                )

                if result.get("image"):
                    image_file = discord.File(result["image"], filename="plan.png")
                    embed.set_image(url="attachment://plan.png")
                    await ctx.send(file=image_file, embed=embed)
                    result["image"].close()
                else:
                    await ctx.send(embed=embed)
            else:
                await ctx.send(f"❌ Planning Failed: {result}")


async def setup(bot):
    await bot.add_cog(WarfareCog(bot))
