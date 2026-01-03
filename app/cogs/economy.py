import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.db_manager import get_session
from app.db.models import House, Game, GamePlayer, User, Fief, Army
from app.services.economy import EconomyService
from app.db.repositories import GameRepo
from app.checks import is_in_house_channel

from app.ui.economy_view import TransactionView
from sqlalchemy.orm.attributes import flag_modified
from app.db.repositories import HouseRepo
import typing

UNIT_PRICES = {
    "infantry": {"buy": 30, "sell": 7, "manpower_cost": 1},
    "archers": {"buy": 25, "sell": 5, "manpower_cost": 1},
    "cavalry": {"buy": 50, "sell": 12, "manpower_cost": 1},
    "ships": {"buy": 1200, "sell": 350, "manpower_cost": 0},
}
VALID_UNITS = list(UNIT_PRICES.keys())


# Assuming this helper is defined in your project, similar to WarfareCog
async def is_gm(ctx):
    async with get_session() as session:
        user = await session.scalar(
            select(User).where(User.discord_id == ctx.author.id)
        )
        return user and user.is_gm


class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _get_player_house(self, session, ctx) -> House | None:
        """Correctly fetches the player's active house for the current game."""
        game = await GameRepo.get_active_game(session, ctx.guild.id)
        if not game:
            return None

        user = await session.scalar(
            select(User).where(User.discord_id == ctx.author.id)
        )
        if not user:
            return None

        player = await session.scalar(
            select(GamePlayer)
            .where(
                GamePlayer.user_id == user.user_id, GamePlayer.game_id == game.game_id
            )
            .options(selectinload(GamePlayer.house))
        )
        return player.house if player else None

    @commands.command(
        name="market", help="Displays the current prices for buying and selling units."
    )
    async def market(self, ctx):
        """Displays the troop and ship market prices."""
        embed = discord.Embed(
            title="The Westeros Market",
            description="Buy mercenaries to bolster your ranks or disband troops for emergency funds.",
            color=discord.Color.gold(),
        )

        for unit, prices in UNIT_PRICES.items():
            buy_price = f"{prices['buy']} Gold"
            sell_price = f"{prices['sell']} Gold"
            manpower = (
                f"(Uses {prices['manpower_cost']} Manpower)"
                if prices["manpower_cost"] > 0
                else "(No Manpower Cost)"
            )

            field_value = (
                f"**Buy (Hire):** {buy_price} {manpower}\n"
                f"**Sell (Disband):** {sell_price}"
            )
            embed.add_field(name=unit.capitalize(), value=field_value, inline=True)

        embed.set_footer(
            text="Selling troops returns manpower. Buying troops consumes it."
        )
        await ctx.send(embed=embed)

    @commands.command(
        name="sell",
        help="Disband troops from an army for gold. Usage: !sell [army_id] [type] [amount]",
    )
    async def sell(self, ctx, army_id: int, unit_type: str, amount: int):
        unit_type = unit_type.lower()
        if amount <= 0:
            return await ctx.send("❌ Amount must be greater than zero.")
        if unit_type not in VALID_UNITS:
            return await ctx.send(
                f"❌ Invalid unit type. Must be one of: `{', '.join(VALID_UNITS)}`"
            )

        async with get_session() as session:
            # --- FIX #1: Correctly look up the player's house ---
            player_house = await self._get_player_house(session, ctx)
            if not player_house:
                return await ctx.send("❌ You do not have a house in this game.")

            army = await session.get(Army, army_id)
            if not army:
                return await ctx.send(f"❌ Army with ID `{army_id}` not found.")
            if army.house_id != player_house.house_id:
                return await ctx.send("❌ You do not own this army.")

            # --- FIX #2: Add validation for selling ships with cargo ---
            if (
                unit_type == "ships"
                and army.cargo
                and (
                    army.cargo.get("troop_count", 0) > 0 or army.cargo.get("prisoners")
                )
            ):
                return await ctx.send(
                    "❌ You cannot sell ships that are carrying troops or prisoners. Please unload them first."
                )

            current_amount = army.composition.get(unit_type, 0)
            if amount > current_amount:
                return await ctx.send(
                    f"❌ Cannot sell {amount} {unit_type}. This army only has {current_amount}."
                )

            gold_earned = amount * UNIT_PRICES[unit_type]["sell"]
            manpower_refunded = amount * UNIT_PRICES[unit_type]["manpower_cost"]

            player_house.treasury += gold_earned
            if manpower_refunded > 0:
                player_house.manpower += manpower_refunded

            army.composition[unit_type] -= amount
            army.troop_count -= amount
            flag_modified(army, "composition")

            response_embed = discord.Embed(
                title="✅ Troops Disbanded", color=discord.Color.dark_red()
            )
            response_embed.add_field(
                name="Gold Earned", value=f"{gold_earned} 💰", inline=True
            )
            if manpower_refunded > 0:
                response_embed.add_field(
                    name="Manpower Refunded",
                    value=f"{manpower_refunded} recruits",
                    inline=True,
                )
            response_embed.set_footer(
                text=f"Your treasury is now {player_house.treasury} Gold."
            )

            if army.troop_count <= 0:
                await session.delete(army)
                response_embed.description = f"You have sold the last **{amount} {unit_type}** from **{army.commander_name}**. The army has been disbanded."
            else:
                response_embed.description = (
                    f"You sold **{amount} {unit_type}** from **{army.commander_name}**."
                )

            await session.commit()
            await ctx.send(embed=response_embed)

    @commands.command(
        name="buy",
        help='Hire mercenaries at a fief you own. Usage: !buy "Fief Name" [type] [amount]',
    )
    async def buy(self, ctx, fief_name: str, unit_type: str, amount: int):
        unit_type = unit_type.lower()
        if amount <= 0:
            return await ctx.send("❌ Amount must be greater than zero.")
        if unit_type not in VALID_UNITS:
            return await ctx.send(
                f"❌ Invalid unit type. Must be one of: `{', '.join(VALID_UNITS)}`"
            )

        async with get_session() as session:
            # --- FIX #1: Correctly look up the player's house ---
            player_house = await self._get_player_house(session, ctx)
            if not player_house:
                return await ctx.send("❌ You do not have a house in this game.")

            game = await GameRepo.get_active_game(session, ctx.guild.id)
            game_id = game.game_id if game else None

            fief = await session.scalar(
                select(Fief).where(Fief.game_id == game_id, Fief.name.ilike(fief_name))
            )
            if not fief:
                return await ctx.send(
                    f"❌ Fief named `{fief_name}` not found in this game."
                )
            if fief.owner_id != player_house.house_id:
                return await ctx.send(f"❌ You do not own {fief.name}.")

            gold_cost = amount * UNIT_PRICES[unit_type]["buy"]
            manpower_cost = amount * UNIT_PRICES[unit_type]["manpower_cost"]

            if player_house.treasury < gold_cost:
                return await ctx.send(
                    f"❌ You cannot afford this. Cost: {gold_cost} Gold. You have: {player_house.treasury} Gold."
                )
            if manpower_cost > 0 and player_house.manpower < manpower_cost:
                return await ctx.send(
                    f"❌ You do not have enough recruits. Cost: {manpower_cost} Manpower. You have: {player_house.manpower}."
                )

            army_type = "SEA" if unit_type == "ships" else "LAND"
            garrison = await session.scalar(
                select(Army).where(
                    Army.house_id == player_house.house_id,
                    Army.location_x == fief.location_x,
                    Army.location_y == fief.location_y,
                    Army.status.in_(["GARRISONED", "DOCKED"]),
                    Army.army_type == army_type,
                )
            )

            if not garrison:
                garrison_status = "DOCKED" if army_type == "SEA" else "GARRISONED"
                garrison_name = (
                    f"Fleet of {fief.name}"
                    if army_type == "SEA"
                    else f"Garrison of {fief.name}"
                )
                garrison = Army(
                    game_id=game_id,
                    house_id=player_house.house_id,
                    commander_name=garrison_name,
                    troop_count=0,
                    composition={},
                    location_x=fief.location_x,
                    location_y=fief.location_y,
                    status=garrison_status,
                    army_type=army_type,
                )
                session.add(garrison)
                await session.flush()

            player_house.treasury -= gold_cost
            if manpower_cost > 0:
                player_house.manpower -= manpower_cost

            garrison.troop_count += amount
            garrison.composition[unit_type] = (
                garrison.composition.get(unit_type, 0) + amount
            )
            flag_modified(garrison, "composition")

            await session.commit()

            embed = discord.Embed(
                title="✅ Units Recruited",
                description=f"You hired **{amount} {unit_type}** at **{fief.name}**. They have been added to the local forces.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Gold Cost", value=f"{gold_cost} 💰", inline=True)
            if manpower_cost > 0:
                embed.add_field(
                    name="Manpower Used", value=f"{manpower_cost} recruits", inline=True
                )
            embed.set_footer(text=f"Your treasury is now {player_house.treasury} Gold.")
            await ctx.send(embed=embed)

    @commands.command()
    async def crown_transfer(self, ctx, target: discord.Member, amount: int):
        """Master of Coin: Transfer money from the Iron Throne to a house."""
        moc_role = discord.utils.get(ctx.guild.roles, name="Master of Coin")
        if not (
            ctx.author.guild_permissions.administrator
            or (moc_role and moc_role in ctx.author.roles)
        ):
            return await ctx.send("❌ You are not the Master of Coin.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Find Royal Treasury
            stmt_crown = (
                select(House)
                .join(Fief)
                .where(Fief.name == "King's Landing", House.game_id == game.game_id)
            )
            crown_house = (await session.execute(stmt_crown)).scalars().first()
            if not crown_house or crown_house.treasury < amount:
                return await ctx.send("❌ Royal Treasury insufficient.")

            # 2. Find Target and their Locked Channel
            stmt_target = (
                select(GamePlayer)
                .join(User)
                .where(User.discord_id == target.id, GamePlayer.game_id == game.game_id)
                .options(selectinload(GamePlayer.house))
            )
            target_player = (await session.execute(stmt_target)).scalars().first()
            if not target_player or not target_player.house:
                return await ctx.send(f"❌ {target.mention} does not control a House.")

            # 3. Execute
            crown_house.treasury -= amount
            target_player.house.treasury += amount
            await session.commit()

            # 4. Notify in Private Quarters via ID
            if target_player.private_channel_id:
                chan = self.bot.get_channel(target_player.private_channel_id)
                if chan:
                    embed = discord.Embed(
                        title="💰 Royal Grant", color=discord.Color.gold()
                    )
                    embed.description = f"The Master of Coin has transferred **{amount} Gold** from the Iron Throne to your treasury."
                    await chan.send(content=target.mention, embed=embed)

            await ctx.send(
                f"✅ Transferred **{amount} gold** to **{target_player.house.name}**."
            )

    @commands.command(name="year_end")
    @commands.has_permissions(administrator=True)
    async def year_end(self, ctx):
        """
        GM Tool: Triggers the fiscal year.
        """
        status_msg = await ctx.send(
            "💰 **Calculating Fiscal Year (Income, Integration, Taxes)...**"
        )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            service = EconomyService(session)

            report_pages = await service.run_fiscal_year(game.game_id)

            game.current_year += 1
            await session.commit()

            await status_msg.delete()

            await ctx.send(f"📅 **Year {game.current_year} Begins!**")

            for page in report_pages:
                if len(page) > 2000:
                    await ctx.send(page[:2000])
                    await ctx.send(page[2000:])
                else:
                    await ctx.send(page)

    @commands.command(name="stop_tax")
    @commands.check(is_in_house_channel)
    async def stop_tax(self, ctx):
        """Toggle paying taxes to your liege."""
        async with get_session() as session:
            stmt = (
                select(House)
                .join(GamePlayer)
                .join(User)
                .where(User.discord_id == ctx.author.id)
            )
            house = (await session.execute(stmt)).scalars().first()

            if not house:
                return await ctx.send("❌ You do not command a house.")

            house.paying_taxes = not house.paying_taxes
            await session.commit()

            status = "RESUMED" if house.paying_taxes else "STOPPED"
            await ctx.send(
                f"💸 **Tax Status Updated:** You have **{status}** paying taxes to your liege."
            )

    @commands.command(name="punish")
    @commands.has_permissions(administrator=True)
    async def punish_desertion(self, ctx, house_name: str, percent: int):
        """
        Removes X% of troops from ALL armies of a house due to desertion.
        Usage: !punish Stark 10
        """
        if not (1 <= percent <= 100):
            return await ctx.send("❌ Percent must be 1-100.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            from app.db.models import Army  # Ensure Army is imported

            stmt = select(House).where(
                House.name.ilike(house_name), House.game_id == game.game_id
            )
            house = (await session.execute(stmt)).scalars().first()
            if not house:
                return await ctx.send("❌ House not found.")

            stmt_a = select(Army).where(Army.house_id == house.house_id)
            armies = (await session.execute(stmt_a)).scalars().all()

            total_lost = 0
            ratio = 1.0 - (percent / 100.0)

            for army in armies:
                old_count = army.troop_count
                new_count = int(old_count * ratio)
                loss = old_count - new_count

                from app.db.repositories import ArmyRepo  # Ensure ArmyRepo is imported

                new_comp, _ = ArmyRepo._calculate_split(
                    army.composition, new_count, old_count
                )

                army.troop_count = new_count
                army.composition = new_comp
                total_lost += loss

            await session.commit()
            await ctx.send(
                f"📉 **Desertion:** House {house.name} has lost **{total_lost}** men ({percent}%)."
            )

    @commands.command(name="loot")
    @commands.has_permissions(administrator=True)
    async def loot(self, ctx, amount: int, victim: str, looter: str):
        """
        Transfers gold + announces pillage.
        Usage: !loot 5000 Lannister Greyjoy
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            async def get_h(name):
                s = select(House).where(
                    House.name.ilike(name), House.game_id == game.game_id
                )
                return (await session.execute(s)).scalars().first()

            vic_h = await get_h(victim)
            loot_h = await get_h(looter)

            if not vic_h or not loot_h:
                return await ctx.send("❌ House not found.")

            vic_h.treasury -= amount
            loot_h.treasury += amount
            await session.commit()

            news_chan = discord.utils.get(
                ctx.guild.text_channels, name="news-and-events"
            )
            embed = discord.Embed(
                title="🔥 City Sacked!", color=discord.Color.dark_red()
            )
            embed.description = f"**House {loot_h.name}** has raided the lands of **House {vic_h.name}**!"
            embed.add_field(name="Loot Taken", value=f"💰 {amount} Gold Dragons")

            if news_chan:
                await news_chan.send(embed=embed)
            await ctx.send("✅ Loot transferred.")

    # --- GM ECONOMY COMMANDS ---
    @commands.group(name="gm_economy", invoke_without_command=True)
    @commands.check(is_gm)
    async def gm_economy(self, ctx):
        """GM commands for economic management for NPCs."""
        await ctx.send("GM Economy Subcommands: `stop_tax`.")

    @commands.command(name="check_gold")
    @commands.check(is_in_house_channel)
    async def check_gold(self, ctx, asset_id: int):
        """
        Check the treasury of a specific Army or Fleet you own.
        Usage: !check_gold [ArmyID]
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Simplified Player Lookup
            stmt_p = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
            )
            player = (await session.execute(stmt_p)).scalars().first()
            if not player or not player.claimed_house_id:
                return await ctx.send("❌ You do not command a House.")

            # 2. Get Asset
            service = EconomyService(session)
            asset, gold, name = await service.get_army_gold(asset_id)

            # 3. Ownership Check
            if not asset or asset.house_id != player.claimed_house_id:
                return await ctx.send(
                    "❌ You do not own this Army/Fleet or it does not exist."
                )

            await ctx.send(
                f"💰 **{name} (ID: {asset.army_id})** Treasury: **{gold} Gold**"
            )

    @commands.command(name="transfer_gold")
    @commands.check(is_in_house_channel)
    async def transfer_gold(
        self, ctx, amount: int, target_type: str, *, identifier: str
    ):
        """
        Transfer gold from your House to an Army or another House.
        Requests are sent to the recipient's LOCKED private quarters.
        """
        target_type = target_type.upper()
        if target_type not in ["ARMY", "FIEF", "FLEET"]:
            return await ctx.send("❌ Type must be `army`, `fleet`, or `fief`.")

        if amount <= 0:
            return await ctx.send("❌ Amount must be positive.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Find Source House
            stmt_p = (
                select(GamePlayer)
                .join(User)
                .where(
                    User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id
                )
                .options(selectinload(GamePlayer.house))
            )
            player = (await session.execute(stmt_p)).scalars().first()

            if not player or not player.house:
                return await ctx.send("❌ You have no House.")

            source_house = player.house
            if source_house.treasury < amount:
                return await ctx.send(
                    f"❌ Insufficient funds. House Treasury: {source_house.treasury}"
                )

            service = EconomyService(session)
            final_target_category = ""
            final_target_id = 0
            final_target_name = ""
            target_owner_id = 0

            # 2. Resolve Target (Army or Fief/House)
            if target_type in ["ARMY", "FLEET"]:
                if not identifier.isdigit():
                    return await ctx.send("❌ ID must be a number.")

                army, _, army_name = await service.get_army_gold(int(identifier))
                if not army:
                    return await ctx.send("❌ Army/Fleet not found.")

                final_target_category = "ARMY"
                final_target_id = army.army_id
                final_target_name = army_name
                target_owner_id = army.house_id

            elif target_type == "FIEF":
                stmt_f = (
                    select(Fief)
                    .where(Fief.game_id == game.game_id, Fief.name.ilike(identifier))
                    .options(selectinload(Fief.owner))
                )
                fief = (await session.execute(stmt_f)).scalars().first()

                if not fief or not fief.owner:
                    return await ctx.send(
                        f"❌ Fief '{identifier}' not found or has no owner."
                    )

                final_target_category = "HOUSE"
                final_target_id = fief.owner.house_id
                final_target_name = fief.owner.name
                target_owner_id = fief.owner.house_id

            # 3. Execution Logic
            if target_owner_id == source_house.house_id:
                # --- INTERNAL TRANSFER ---
                if final_target_category == "HOUSE":
                    return await ctx.send(
                        "❌ You cannot transfer money from your House to itself."
                    )

                success, msg = await service.execute_transfer(
                    source_house.house_id,
                    final_target_category,
                    final_target_id,
                    amount,
                )
                await ctx.send(msg if success else f"❌ {msg}")

            else:
                # --- EXTERNAL TRANSFER (USING LOCKED CHANNEL ID) ---
                stmt_recip = (
                    select(GamePlayer)
                    .join(User)
                    .where(
                        GamePlayer.game_id == game.game_id,
                        GamePlayer.claimed_house_id == target_owner_id,
                    )
                )
                recip_p = (await session.execute(stmt_recip)).scalars().first()

                embed = discord.Embed(
                    title="💸 Incoming Transfer Request", color=discord.Color.gold()
                )
                embed.description = f"**{source_house.name}** is sending **{amount} Gold** to **{final_target_name}**."
                embed.set_footer(
                    text="Click below to accept this transfer into your treasury."
                )

                if recip_p:
                    # Delivered to Recipient's Locked Quarters
                    target_channel = self.bot.get_channel(recip_p.private_channel_id)

                    view = TransactionView(
                        source_house.house_id,
                        final_target_category,
                        final_target_id,
                        amount,
                        approver_discord_id=recip_p.user.discord_id,
                    )

                    if target_channel:
                        await target_channel.send(
                            content=f"<@{recip_p.user.discord_id}>",
                            embed=embed,
                            view=view,
                        )
                        await ctx.send(
                            f"✅ **Request Sent:** A raven was dispatched to the private quarters of **{final_target_name}**."
                        )
                    else:
                        # Fallback for players without a locked channel ID
                        await ctx.send(
                            f"{recip_p.user.discord_id}, a transfer is waiting for your consent.",
                            embed=embed,
                            view=view,
                        )
                else:
                    # NPC Owned (Sends to GMs)
                    gm_chan = discord.utils.get(
                        ctx.guild.text_channels, name="gm-alerts"
                    )
                    if not gm_chan:
                        return await ctx.send("❌ #gm-alerts channel missing.")

                    view = TransactionView(
                        source_house.house_id,
                        final_target_category,
                        final_target_id,
                        amount,
                        is_gm_approval=True,
                    )
                    await gm_chan.send(
                        f"🔔 **NPC Interaction:** Transfer request for **{final_target_name}** (NPC).",
                        embed=embed,
                        view=view,
                    )
                    await ctx.send("✅ Transfer request sent to GMs for approval.")

    # --- 3. GM ECONOMY COMMANDS ---

    @commands.group(name="gm_econ", invoke_without_command=True)
    @commands.check(is_gm)
    async def gm_econ(self, ctx):
        """GM Economy Hub."""
        await ctx.send(
            "**GM Economy Subcommands:**\n"
            "`!gm_econ check [HouseName/ID]` - Audit treasury\n"
            "`!gm_econ transfer [FromID] [Amount] [ARMY/HOUSE] [ToID]` - Force transfer\n"
            "`!gm_econ set_tax [House] [Percent]` - Set house contribution\n"
            "`!gm_econ set_vassal_tax [Liege] [Percent]` - Set all vassals of a liege\n"
            "`!gm_econ buy/sell` - Force unit management\n"
            "`!gm_econ stop_tax [House]` - Toggle tax status"
        )

    @gm_econ.command(name="check")
    async def gm_check(self, ctx, *, identifier: str):
        """GM Audit: Checks gold for a House (name/ID) or Army (ID)."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Try finding a HOUSE first
            stmt_house = select(House).where(House.game_id == game.game_id)
            if identifier.isdigit():
                stmt_house = stmt_house.where(House.house_id == int(identifier))
            else:
                stmt_house = stmt_house.where(House.name.ilike(identifier))

            house = (await session.execute(stmt_house)).scalars().first()
            if house:
                return await ctx.send(
                    f"🕵️ **GM Audit:** House **{house.name}** (ID: {house.house_id})\nTreasury: **{house.treasury} Gold**"
                )

            # 2. Try finding an ARMY
            if identifier.isdigit():
                service = EconomyService(session)
                army, gold, name = await service.get_army_gold(int(identifier))
                if army:
                    owner = army.house.name if army.house else "Unknown"
                    return await ctx.send(
                        f"🕵️ **GM Audit:** Army **{name}** (ID: {army.army_id})\nOwner: {owner}\nTreasury: **{gold} Gold**"
                    )

            await ctx.send(f"❌ Could not find House or Army matching '{identifier}'.")

    @gm_econ.command(name="transfer")
    async def gm_transfer(
        self, ctx, source_house_id: int, amount: int, target_type: str, target_id: int
    ):
        """GM: Force transfer gold between entities."""
        target_type = target_type.upper()
        if target_type == "FLEET":
            target_type = "ARMY"
        if target_type not in ["ARMY", "HOUSE"]:
            return await ctx.send("❌ Target type must be ARMY or HOUSE.")

        async with get_session() as session:
            service = EconomyService(session)
            success, msg = await service.execute_transfer(
                source_house_id, target_type, target_id, amount
            )
            await ctx.send(msg if success else f"❌ Error: {msg}")

    @commands.command(name="set_tax")
    @commands.check(is_in_house_channel)
    async def set_own_tax(self, ctx, percent: int):
        """Set the tax rate YOU are willing to pay your Liege."""
        if not (0 <= percent <= 100):
            return await ctx.send("❌ Percentage must be 0-100.")

        async with get_session() as session:
            # Simplified Player Lookup
            stmt = (
                select(GamePlayer)
                .join(User)
                .where(User.discord_id == ctx.author.id, GamePlayer.is_primary == True)
                .options(selectinload(GamePlayer.house))
            )
            player = (await session.execute(stmt)).scalars().first()

            if not player or not player.house:
                return await ctx.send("❌ You do not have a House.")

            player.house.tax_rate = percent / 100.0
            await session.commit()
            await ctx.send(
                f"📉 **Tax Adjustment:** You have set your contribution to your liege to **{percent}%**."
            )

    @commands.command(name="set_vassal_tax")
    @commands.check(is_in_house_channel)
    async def set_vassal_tax(self, ctx, percent: int):
        """Sets tax for vassals and notifies them via Locked Channel IDs."""
        if not (0 <= percent <= 100):
            return await ctx.send("❌ 0-100 only.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            liege_house = await self._get_player_house(session, ctx)
            if not liege_house:
                return await ctx.send("❌ No House.")

            # Find Vassals AND their locked channel IDs
            stmt_v = (
                select(House, GamePlayer.private_channel_id)
                .outerjoin(GamePlayer, House.house_id == GamePlayer.claimed_house_id)
                .where(
                    House.liege_id == liege_house.house_id,
                    House.game_id == game.game_id,
                )
            )
            vassal_results = (await session.execute(stmt_v)).all()

            if not vassal_results:
                return await ctx.send("❌ No vassals found.")

            new_rate = percent / 100.0
            for house, chan_id in vassal_results:
                house.tax_rate = new_rate

                # Notify via Locked ID (No slugs!)
                if chan_id:
                    chan = self.bot.get_channel(chan_id)
                    if chan:
                        try:
                            await chan.send(
                                f"📉 **Tax Update:** Your liege, **{liege_house.name}**, has set your tax rate to **{percent}%**."
                            )
                        except:
                            pass

            await session.commit()

            # Public Announcement
            news_chan = discord.utils.get(
                ctx.guild.text_channels, name="news-and-events"
            )
            if news_chan:
                embed = discord.Embed(title="📜 Tax Reform", color=discord.Color.gold())
                embed.description = f"**House {liege_house.name}** has changed the tax rate for their vassals to **{percent}%**."
                await news_chan.send(embed=embed)

            await ctx.send(
                f"✅ Updated tax to **{percent}%** for **{len(vassal_results)}** vassals."
            )

    @gm_econ.command(name="set_tax")
    async def gm_set_tax(self, ctx, house_identifier: str, percent: int):
        """GM: Set a specific house's tax rate."""
        if not (0 <= percent <= 100):
            return await ctx.send("❌ Percent 0-100.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = select(House).where(House.game_id == game.game_id)
            if house_identifier.isdigit():
                stmt = stmt.where(House.house_id == int(house_identifier))
            else:
                stmt = stmt.where(House.name.ilike(house_identifier))

            house = (await session.execute(stmt)).scalars().first()
            if not house:
                return await ctx.send(f"❌ House '{house_identifier}' not found.")

            house.tax_rate = percent / 100.0
            await session.commit()
            await ctx.send(f"✅ Set tax rate for **{house.name}** to **{percent}%**.")

    @gm_econ.command(name="set_vassal_tax")
    async def gm_set_vassal_tax(self, ctx, liege_identifier: str, percent: int):
        """GM: Set tax rate for all vassals of a liege and notify via Locked IDs."""
        if not (0 <= percent <= 100):
            return await ctx.send("❌ Percent 0-100.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)

            # 1. Find Liege
            stmt_l = select(House).where(House.game_id == game.game_id)
            if liege_identifier.isdigit():
                stmt_l = stmt_l.where(House.house_id == int(liege_identifier))
            else:
                stmt_l = stmt_l.where(House.name.ilike(liege_identifier))
            liege = (await session.execute(stmt_l)).scalars().first()

            if not liege:
                return await ctx.send(f"❌ Liege '{liege_identifier}' not found.")

            # 2. Find Vassals AND IDs
            stmt_v = (
                select(House, GamePlayer.private_channel_id)
                .outerjoin(GamePlayer, House.house_id == GamePlayer.claimed_house_id)
                .where(House.liege_id == liege.house_id, House.game_id == game.game_id)
            )
            vassals = (await session.execute(stmt_v)).all()

            if not vassals:
                return await ctx.send(f"❌ {liege.name} has no vassals.")

            new_rate = percent / 100.0
            for v_house, chan_id in vassals:
                v_house.tax_rate = new_rate
                if chan_id:
                    chan = self.bot.get_channel(chan_id)
                    if chan:
                        try:
                            await chan.send(
                                f"📉 **Tax Update:** Your liege, **{liege.name}**, has set your tax rate to **{percent}%**."
                            )
                        except:
                            pass

            await session.commit()
            await ctx.send(
                f"✅ Updated tax to **{percent}%** for all vassals of **{liege.name}**."
            )

    @gm_econ.command(name="stop_tax")
    async def gm_stop_tax(self, ctx, house_identifier: str):
        """GM: Toggle tax status for a house."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            stmt = select(House).where(House.game_id == game.game_id)
            if house_identifier.isdigit():
                stmt = stmt.where(House.house_id == int(house_identifier))
            else:
                stmt = stmt.where(House.name.ilike(house_identifier))

            house = (await session.execute(stmt)).scalars().first()
            if not house:
                return await ctx.send(f"❌ House '{house_identifier}' not found.")

            house.paying_taxes = not house.paying_taxes
            await session.commit()
            status = "RESUMED" if house.paying_taxes else "STOPPED"
            await ctx.send(f"✅ House **{house.name}** has **{status}** paying taxes.")

    @gm_econ.command(name="tax_income")
    async def gm_tax_income(self, ctx, *, house_identifier: str):
        """GM: Audit expected tax income for any house."""
        async with get_session() as session:
            house = await HouseRepo.get_house_by_name_or_id(session, house_identifier)
            if not house:
                return await ctx.send(f"❌ House `{house_identifier}` not found.")

            service = EconomyService(session)
            vassals, total_tax = await service.calculate_tax_income_for_house(
                house.house_id
            )

            if not vassals:
                return await ctx.send(f"**{house.name}** has no vassals.")

            embed = discord.Embed(
                title=f"GM | Tax Income for House {house.name}",
                color=discord.Color.blue(),
            )
            report_lines = [
                f"✅ **{v_name}**: {v_income} Gold * {int(v_rate*100)}% = **{tax_val}**"
                for v_name, v_income, v_rate, tax_val in vassals
            ]
            embed.description = "\n".join(report_lines)
            embed.set_footer(text=f"Total Expected: {total_tax} Gold")
            await ctx.send(embed=embed)
