import discord
from discord.ext import commands
from app.db.db_manager import get_session
from sqlalchemy import select
from app.db.models import House, Game, GamePlayer, User
from app.services.setup_service import SetupService
from app.services.scenario_service import ScenarioService
from app.db.repositories import GameRepo
from app.db.models import House, Game, GamePlayer, User, Fief, Army


class PoliticsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- GM TOOL ---
    @commands.command(name="coronate")
    @commands.has_permissions(administrator=True)
    async def coronate(self, ctx, target: discord.Member):
        """
        GM Only: Assigns the Iron Throne to a player.
        """
        role = discord.utils.get(ctx.guild.roles, name="IronThrone")
        if not role:
            await ctx.send("❌ Role `IronThrone` not found. Run `!setup_game`.")
            return

        # 1. Remove from old King(s)
        for member in ctx.guild.members:
            if role in member.roles:
                await member.remove_roles(role)

        # 2. Assign to new King
        await target.add_roles(role)

        # 3. Announcement
        decree_channel = discord.utils.get(
            ctx.guild.text_channels, name="royal-decrees"
        )
        msg = f"👑 **All Hail His Grace!** {target.mention} now sits the Iron Throne."

        if decree_channel:
            await decree_channel.send(msg)
        await ctx.send(msg)

    # --- KING TOOLS ---
    @commands.command(name="appoint")
    async def appoint_council(self, ctx, target: discord.Member, *, title: str):
        """
        King Only: Appoint a Small Council member.
        Usage: !appoint @Tywin Hand of the King
        """
        # 1. Verify Authority
        king_role = discord.utils.get(ctx.guild.roles, name="IronThrone")
        if king_role not in ctx.author.roles:
            await ctx.send("❌ You do not sit the Iron Throne.")
            return

        # 2. Validate Title
        valid_titles = [
            "Hand of the King",
            "Master of Coin",
            "Master of Whisperers",
            "Master of Ships",
            "Master of Laws",
            "Lord Commander",
            "Grand Maester",
        ]

        # Fuzzy match logic
        selected_title = next(
            (t for t in valid_titles if t.lower() == title.lower()), None
        )

        if not selected_title:
            await ctx.send(f"❌ Invalid Title. Choose from:\n{', '.join(valid_titles)}")
            return

        # 3. Fetch Roles
        title_role = discord.utils.get(ctx.guild.roles, name=selected_title)
        access_role = discord.utils.get(
            ctx.guild.roles, name="SmallCouncil"
        )  # Gives channel access

        if not title_role or not access_role:
            await ctx.send("❌ Roles missing. Ask GM to run `!setup_game` again.")
            return

        # 4. Assign Roles
        try:
            await target.add_roles(title_role, access_role)

            # 5. Announce
            decree_channel = discord.utils.get(
                ctx.guild.text_channels, name="royal-decrees"
            )
            msg = f"📜 **Royal Decree:** His Grace appoints {target.mention} as **{selected_title}**."

            if decree_channel:
                await decree_channel.send(msg)
            else:
                await ctx.send(msg)

        except discord.Forbidden:
            await ctx.send(
                "❌ Bot permission error: Put the Bot Role higher than Council roles."
            )

    @commands.command(name="dismiss")
    async def dismiss_council(self, ctx, target: discord.Member):
        """
        King Only: Remove someone from the Small Council.
        """
        king_role = discord.utils.get(ctx.guild.roles, name="IronThrone")
        if king_role not in ctx.author.roles:
            await ctx.send("❌ You do not sit the Iron Throne.")
            return

        # Remove all council-related roles
        council_roles = [
            "SmallCouncil",
            "Hand of the King",
            "Master of Coin",
            "Master of Whisperers",
            "Master of Ships",
            "Master of Laws",
            "Lord Commander",
            "Grand Maester",
        ]

        removed_roles = []
        for role_name in council_roles:
            role = discord.utils.get(ctx.guild.roles, name=role_name)
            if role and role in target.roles:
                await target.remove_roles(role)
                removed_roles.append(role_name)

        if removed_roles:
            await ctx.send(
                f"🚫 {target.mention} has been dismissed from: {', '.join(removed_roles)}."
            )
        else:
            await ctx.send(f"❌ {target.display_name} holds no council seats.")

    # @commands.command(name="grant_title")
    # async def grant_title(self, ctx, castle_name: str, *, target_name: str):
    #     """
    #     Grants a Fief.
    #     Usage: !grant_title Dragonstone @Stannis
    #     OR:    !grant_title Dragonstone Stannis
    #     """
    #     async with get_session() as session:
    #         # 1. Resolve Target (Mention or Name)
    #         target_user_id = None

    #         # Check if it's a mention (<@123456>)
    #         if len(ctx.message.mentions) > 0:
    #             target_user_id = ctx.message.mentions[0].id
    #         else:
    #             # Look up by Character Name in DB
    #             from app.db.models import Character, GamePlayer, User

    #             stmt_char = (
    #                 select(GamePlayer)
    #                 .join(Character)
    #                 .join(User)
    #                 .where(
    #                     Character.name.ilike(target_name),
    #                     GamePlayer.game_id
    #                     == (
    #                         select(Game.game_id)
    #                         .where(
    #                             Game.guild_id == ctx.guild.id, Game.is_active == True
    #                         )
    #                         .scalar_subquery()
    #                     ),
    #                 )
    #             )
    #             target_p = (await session.execute(stmt_char)).scalars().first()
    #             if target_p:
    #                 # We need the Discord ID to ping them later
    #                 u = await session.get(User, target_p.user_id)
    #                 target_user_id = u.discord_id

    #         if not target_user_id:
    #             await ctx.send(f"❌ Could not find a player named **{target_name}**.")
    #             return

    #         # 2. Find Sender's House
    #         # ... (Rest of logic is same, just use target_user_id) ...

    #         stmt_sender = (
    #             select(GamePlayer)
    #             .join(User)
    #             .where(
    #                 User.discord_id == ctx.author.id,
    #                 GamePlayer.game_id
    #                 == (
    #                     select(Game.game_id)
    #                     .where(Game.guild_id == ctx.guild.id, Game.is_active == True)
    #                     .scalar_subquery()
    #                 ),
    #             )
    #         )
    #         sender_p = (await session.execute(stmt_sender)).scalars().first()

    #         if not sender_p or not sender_p.is_primary:
    #             await ctx.send("❌ You do not have authority.")
    #             return

    #         # 3. Find Fief
    #         stmt_fief = select(Fief).where(
    #             Fief.name.ilike(castle_name), Fief.owner_id == sender_p.claimed_house_id
    #         )
    #         fief = (await session.execute(stmt_fief)).scalars().first()

    #         if not fief:
    #             await ctx.send(f"❌ You do not own **{castle_name}**.")
    #             return

    #         # 4. Find Target Player Entry
    #         stmt_target = (
    #             select(GamePlayer)
    #             .join(User)
    #             .where(
    #                 User.discord_id == target_user_id,
    #                 GamePlayer.game_id == sender_p.game_id,
    #             )
    #         )
    #         target_p = (await session.execute(stmt_target)).scalars().first()

    #         if not target_p or not target_p.claimed_house_id:
    #             await ctx.send(f"❌ Target has not claimed a faction yet.")
    #             return

    #         target_house_id = target_p.claimed_house_id

    #         # 5. EXECUTE
    #         fief.owner_id = target_house_id

    #         stmt_army = select(Army).where(
    #             Army.house_id == sender_p.claimed_house_id,
    #             Army.location_x == fief.location_x,
    #             Army.location_y == fief.location_y,
    #             Army.status == "GARRISONED",
    #         )
    #         garrisons = (await session.execute(stmt_army)).scalars().all()
    #         for army in garrisons:
    #             army.house_id = target_house_id
    #             army.commander_name = f"Garrison of {fief.name}"

    #         target_house = await session.get(House, target_house_id)
    #         target_house.liege_id = sender_p.claimed_house_id

    #         # Money
    #         local_gold = fief.base_income * 2
    #         sender_house = await session.get(House, sender_p.claimed_house_id)

    #         money_msg = "but coffers were empty."
    #         if sender_house.treasury >= local_gold:
    #             sender_house.treasury -= local_gold
    #             target_house.treasury += local_gold
    #             money_msg = f"plus **{local_gold}** gold."

    #         await session.commit()

    #         # Get Discord Member object for display
    #         target_member = ctx.guild.get_member(target_user_id)
    #         name_display = target_member.mention if target_member else target_name

    #         await ctx.send(
    #             f"📜 **Proclamation:** **{castle_name}** is granted to {name_display}, {money_msg}"
    #         )
    #         break
    @commands.command(name="grant_title")
    async def grant_title(self, ctx, *, input_str: str):
        """
        Grants a Fief. Smartly detects multi-word castle names.
        Usage: !grant_title Storm's End Renly
        """
        async with get_session() as session:
            # 1. Find Sender's House
            from app.db.models import GamePlayer, User, House, Fief, Army, Game

            stmt_sender = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id,
                    GamePlayer.game_id
                    == (
                        select(Game.game_id)
                        .where(Game.guild_id == ctx.guild.id, Game.is_active == True)
                        .scalar_subquery()
                    ),
                )
            )
            sender_p = (await session.execute(stmt_sender)).scalars().first()

            if not sender_p or not sender_p.is_primary:
                await ctx.send("❌ You do not have authority to grant titles.")
                return

            # 2. SMART PARSING: Find which Fief they are talking about
            # Fetch all fiefs owned by this player
            stmt_fiefs = select(Fief).where(Fief.owner_id == sender_p.claimed_house_id)
            owned_fiefs = (await session.execute(stmt_fiefs)).scalars().all()

            # Sort by name length (Longest first) to avoid matching "King" instead of "King's Landing"
            owned_fiefs.sort(key=lambda x: len(x.name), reverse=True)

            target_fief = None
            target_name = ""

            for f in owned_fiefs:
                # Check if the input string starts with this castle name (Case insensitive)
                if input_str.lower().startswith(f.name.lower()):
                    target_fief = f
                    # The rest of the string is the target name
                    # We slice the input string by the length of the castle name
                    target_name = input_str[len(f.name) :].strip()
                    break

            if not target_fief:
                await ctx.send(
                    f"❌ You do not own a castle matching the start of: **{input_str}**"
                )
                return

            if not target_name:
                await ctx.send(
                    f"❌ You must specify a player to grant **{target_fief.name}** to."
                )
                return

            # 3. Resolve Target (Mention or Name)
            target_user_id = None

            # Check for Mention logic inside the stripped string
            # (Discord converts @User to <@12345> in the string)
            import re

            mention_match = re.search(r"<@!?(\d+)>", target_name)

            if mention_match:
                target_user_id = int(mention_match.group(1))
            else:
                # Look up by Character Name in DB
                from app.db.models import Character

                stmt_char = (
                    select(GamePlayer)
                    .join(Character)
                    .join(User)
                    .where(
                        Character.name.ilike(target_name),
                        GamePlayer.game_id == sender_p.game_id,
                    )
                )
                target_p = (await session.execute(stmt_char)).scalars().first()
                if target_p:
                    # We need the Discord ID from the User object
                    u = await session.get(User, target_p.user_id)
                    target_user_id = u.discord_id

            if not target_user_id:
                await ctx.send(f"❌ Could not find a player named **{target_name}**.")
                return

            # 4. Find Target Player Entry (Re-verify)
            stmt_final_target = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == target_user_id,
                    GamePlayer.game_id == sender_p.game_id,
                )
            )
            target_p = (await session.execute(stmt_final_target)).scalars().first()

            if not target_p or not target_p.claimed_house_id:
                await ctx.send(f"❌ Target has not claimed a faction yet.")
                return

            target_house_id = target_p.claimed_house_id

            # 5. EXECUTE TRANSFER
            target_fief.owner_id = target_house_id

            # Transfer Garrison
            stmt_army = select(Army).where(
                Army.house_id == sender_p.claimed_house_id,
                Army.location_x == target_fief.location_x,
                Army.location_y == target_fief.location_y,
                Army.status == "GARRISONED",
            )
            garrisons = (await session.execute(stmt_army)).scalars().all()
            for army in garrisons:
                army.house_id = target_house_id
                army.commander_name = f"Garrison of {target_fief.name}"

            # Set Liege
            target_house = await session.get(House, target_house_id)
            target_house.liege_id = sender_p.claimed_house_id

            # Money Transfer
            local_gold = target_fief.base_income * 2
            sender_house = await session.get(House, sender_p.claimed_house_id)

            money_msg = "but coffers were empty."
            if sender_house.treasury >= local_gold:
                sender_house.treasury -= local_gold
                target_house.treasury += local_gold
                money_msg = f"plus **{local_gold}** gold."

            await session.commit()

            # Get Discord Member object for display
            target_member = ctx.guild.get_member(target_user_id)
            display_name = target_member.mention if target_member else target_name

            await ctx.send(
                f"📜 **Proclamation:** **{target_fief.name}** is granted to {display_name}, {money_msg}"
            )
            


async def setup(bot):
    await bot.add_cog(PoliticsCog(bot))
