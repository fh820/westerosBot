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
from app.ui.fief_view import SimplePaginationView

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

    MONEY_TYPES = {"FIEF", "ARMY", "HOUSE"}

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

    def _gold_help_text(self, is_gm: bool = False) -> str:
        prefix = "!gm_gold" if is_gm else "!gold"
        return (
            f"**Gold Commands**\n"
            f"`{prefix} send <amount> from <source> to <target>`\n"
            f"`{prefix} check <source>`\n\n"
            "**Sources and targets:**\n"
            "`fief Winterfell`, `army 123`, `fleet 456`, `house Stark`\n\n"
            "**Examples:**\n"
            f"`{prefix} send 500 from fief Winterfell to army 123`\n"
            f"`{prefix} send 300 from army 123 to local_fief`\n"
            f"`{prefix} send 1000 from fief Winterfell to fief \"Deepwood Motte\"`\n"
            f"`{prefix} check fief Winterfell`"
        )

    async def _ensure_money_channel(self, ctx) -> bool:
        if await is_in_house_channel(ctx):
            return True
        await ctx.send(
            "Error: Use money commands in your house quarters or an allowed bot channel."
        )
        return False

    def _split_gold_route(self, route: str) -> tuple[str | None, str | None]:
        text = route.strip()
        lowered = text.lower()
        if not lowered.startswith("from "):
            return None, None

        body = text[5:].strip()
        lowered_body = body.lower()
        marker = " to "
        marker_index = lowered_body.find(marker)
        if marker_index == -1:
            return None, None

        source_text = body[:marker_index].strip().strip('"')
        target_text = body[marker_index + len(marker) :].strip().strip('"')
        if not source_text or not target_text:
            return None, None
        return source_text, target_text

    async def _resolve_house_endpoint(self, session, game, ident: str) -> House | None:
        stmt = select(House).where(House.game_id == game.game_id)
        if ident.isdigit():
            stmt = stmt.where(House.house_id == int(ident))
        else:
            stmt = stmt.where(House.name.ilike(ident))
        return (await session.execute(stmt)).scalars().first()

    async def _resolve_fief_endpoint(self, session, game, ident: str) -> Fief | None:
        stmt = select(Fief).where(Fief.game_id == game.game_id)
        if ident.isdigit():
            stmt = stmt.where(Fief.fief_id == int(ident))
        else:
            stmt = stmt.where(Fief.name.ilike(ident))
        return (await session.execute(stmt)).scalars().first()

    async def _resolve_army_endpoint(self, session, game, ident: str) -> Army | None:
        if not ident.isdigit():
            return None
        stmt = select(Army).where(
            Army.game_id == game.game_id,
            Army.army_id == int(ident),
        )
        return (await session.execute(stmt)).scalars().first()

    async def _find_local_fief_for_army(self, session, game, army: Army) -> Fief | None:
        stmt = select(Fief).where(
            Fief.game_id == game.game_id,
            Fief.location_x == army.location_x,
            Fief.location_y == army.location_y,
        )
        return (await session.execute(stmt)).scalars().first()

    def _endpoint_label(self, endpoint: dict) -> str:
        obj = endpoint["obj"]
        etype = endpoint["type"]
        if etype == "FIEF":
            return f"Fief {obj.name} (ID: {obj.fief_id})"
        if etype == "ARMY":
            noun = "Fleet" if getattr(obj, "army_type", "") == "SEA" else "Army"
            name = obj.commander_name or f"{noun} {obj.army_id}"
            return f"{noun} {name} (ID: {obj.army_id})"
        if etype == "HOUSE":
            return f"House {obj.name} Treasury (ID: {obj.house_id})"
        return "Unknown"

    def _endpoint_owner_id(self, endpoint: dict) -> int | None:
        obj = endpoint["obj"]
        if endpoint["type"] == "FIEF":
            return obj.owner_id
        if endpoint["type"] == "ARMY":
            return obj.house_id
        if endpoint["type"] == "HOUSE":
            return obj.house_id
        return None

    async def _resolve_money_endpoint(
        self,
        session,
        game,
        raw_text: str,
        *,
        player_house: House | None = None,
        source_endpoint: dict | None = None,
    ) -> tuple[dict | None, str | None]:
        text = raw_text.strip().strip('"')
        lowered = text.lower()

        if lowered in {"capital", "capital_fief", "default_fief"}:
            if not player_house:
                return None, "`capital` can only be used by a player with a house."
            stmt = (
                select(Fief)
                .where(Fief.owner_id == player_house.house_id)
                .order_by(Fief.fief_id.asc())
                .limit(1)
            )
            fief = (await session.execute(stmt)).scalars().first()
            if not fief:
                return None, f"House {player_house.name} has no fiefs."
            return {"type": "FIEF", "id": fief.fief_id, "obj": fief}, None

        if lowered in {"treasury", "house_treasury"}:
            if not player_house:
                return None, "`treasury` can only be used by a player with a house."
            return (
                {"type": "HOUSE", "id": player_house.house_id, "obj": player_house},
                None,
            )

        if lowered == "local_fief":
            if not source_endpoint or source_endpoint["type"] != "ARMY":
                return None, "`local_fief` only works when the source is an army/fleet."
            fief = await self._find_local_fief_for_army(
                session, game, source_endpoint["obj"]
            )
            if not fief:
                return None, "That army/fleet is not standing at a registered fief."
            return {"type": "FIEF", "id": fief.fief_id, "obj": fief}, None

        parts = text.split(maxsplit=1)
        type_word = parts[0].upper() if parts else ""
        ident = parts[1].strip().strip('"') if len(parts) > 1 else ""

        if type_word == "FLEET":
            type_word = "ARMY"
        if type_word in self.MONEY_TYPES:
            if not ident:
                return None, f"Please provide a name or ID after `{parts[0]}`."
            if type_word == "FIEF":
                fief = await self._resolve_fief_endpoint(session, game, ident)
                if not fief:
                    return None, f"Fief `{ident}` was not found."
                return {"type": "FIEF", "id": fief.fief_id, "obj": fief}, None
            if type_word == "ARMY":
                army = await self._resolve_army_endpoint(session, game, ident)
                if not army:
                    return None, f"Army/Fleet `{ident}` was not found."
                return {"type": "ARMY", "id": army.army_id, "obj": army}, None
            house = await self._resolve_house_endpoint(session, game, ident)
            if not house:
                return None, f"House `{ident}` was not found."
            return {"type": "HOUSE", "id": house.house_id, "obj": house}, None

        if text.isdigit():
            army = await self._resolve_army_endpoint(session, game, text)
            if army:
                return {"type": "ARMY", "id": army.army_id, "obj": army}, None
            return None, f"`{text}` did not match an army/fleet ID."

        fief = await self._resolve_fief_endpoint(session, game, text)
        house = await self._resolve_house_endpoint(session, game, text)
        if fief and house:
            return (
                None,
                f"`{text}` matches both a fief and a house. Use `fief {text}` or `house {text}`.",
            )
        if fief:
            return {"type": "FIEF", "id": fief.fief_id, "obj": fief}, None
        if house:
            return {"type": "HOUSE", "id": house.house_id, "obj": house}, None
        return None, f"`{text}` was not found. Try `fief {text}`, `house {text}`, or `army <id>`."

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
        from sqlalchemy import desc  # Needed for sorting by richest fief

        unit_type = unit_type.lower()
        if amount <= 0:
            return await ctx.send("❌ Amount must be greater than zero.")
        if unit_type not in VALID_UNITS:
            return await ctx.send(
                f"❌ Invalid unit type. Must be one of: `{', '.join(VALID_UNITS)}`"
            )

        async with get_session() as session:
            # 1. Setup Context
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            player_house = await self._get_player_house(session, ctx)
            if not player_house:
                return await ctx.send("❌ You do not have a house in this game.")

            army = await session.get(Army, army_id)
            if not army:
                return await ctx.send(f"❌ Army with ID `{army_id}` not found.")
            if army.house_id != player_house.house_id:
                return await ctx.send("❌ You do not own this army.")

            # 2. Validation (Cargo Check)
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

            # 3. Calculate Value
            gold_earned = amount * UNIT_PRICES[unit_type]["sell"]
            manpower_refunded = amount * UNIT_PRICES[unit_type]["manpower_cost"]

            # If the army is being disbanded, we must also save its existing treasury
            existing_army_gold = army.treasury or 0

            # Will the army survive this sale?
            army_will_survive = (army.troop_count - amount) > 0

            # 4. Determine Gold Destination
            dest_name = ""
            dest_balance = 0

            # Check for Local Fief (Realism Preference)
            stmt_fief = select(Fief).where(
                Fief.game_id == game.game_id,
                Fief.location_x == army.location_x,
                Fief.location_y == army.location_y,
            )
            local_fief = (await session.execute(stmt_fief)).scalars().first()

            if local_fief and local_fief.owner_id == player_house.house_id:
                # SCENARIO A: At home. Put gold in local castle.
                deposit_amt = gold_earned
                if not army_will_survive:
                    deposit_amt += (
                        existing_army_gold  # Recover army's carry if disbanding
                    )

                local_fief.treasury = (local_fief.treasury or 0) + deposit_amt
                dest_name = f"**{local_fief.name}** Treasury"
                dest_balance = local_fief.treasury

                if not army_will_survive:
                    army.treasury = 0  # Emptied out

            elif army_will_survive:
                # SCENARIO B: In field, army survives. Keep gold on army.
                army.treasury = existing_army_gold + gold_earned
                dest_name = "Army Coffers"
                dest_balance = army.treasury

            else:
                # SCENARIO C: Disbanding in field. Wire transfer to Richest Fief.
                stmt_rich = (
                    select(Fief)
                    .where(Fief.owner_id == player_house.house_id)
                    .order_by(desc(Fief.treasury))
                    .limit(1)
                )
                richest_fief = (await session.execute(stmt_rich)).scalars().first()

                total_salvage = gold_earned + existing_army_gold

                if richest_fief:
                    richest_fief.treasury = (richest_fief.treasury or 0) + total_salvage
                    dest_name = f"**{richest_fief.name}** (Wired)"
                    dest_balance = richest_fief.treasury
                else:
                    # Fallback if they own 0 fiefs (Rare) - Send to House global (safe net)
                    player_house.treasury += total_salvage
                    dest_name = "House Treasury (Safe Net)"
                    dest_balance = player_house.treasury

            # 5. Refund Manpower (Global)
            if manpower_refunded > 0:
                player_house.manpower += manpower_refunded

            # 6. Update Army Data
            army.composition[unit_type] -= amount
            if army.composition[unit_type] <= 0:
                del army.composition[unit_type]

            army.troop_count -= amount
            flag_modified(army, "composition")

            # 7. Construct Response
            response_embed = discord.Embed(
                title="✅ Troops Disbanded", color=discord.Color.dark_red()
            )
            response_embed.add_field(
                name="Gold Earned", value=f"{gold_earned} 💰", inline=True
            )
            response_embed.add_field(name="Deposited To", value=dest_name, inline=True)

            if manpower_refunded > 0:
                response_embed.add_field(
                    name="Manpower Refunded",
                    value=f"{manpower_refunded} recruits",
                    inline=False,
                )

            response_embed.set_footer(
                text=f"New Destination Balance: {dest_balance} Gold"
            )

            if army.troop_count <= 0:
                await session.delete(army)
                response_embed.description = f"You have sold the last **{amount} {unit_type}** from **{army.commander_name}**. The army has been disbanded and all assets transferred."
            else:
                response_embed.description = (
                    f"You sold **{amount} {unit_type}** from **{army.commander_name}**."
                )

            await session.commit()
            await ctx.send(embed=response_embed)

    @commands.command(
        name="buy",
        help="Hire mercenaries at a fief you own. Usage: !buy [Fief Name] [type] [amount]",
    )
    async def buy(self, ctx, *, args: str = None):
        """
        Hire mercenaries. Handles multi-word fief names automatically.
        Usage: !buy Storm's End Infantry 200
        """
        if not args:
            return await ctx.send("❌ Usage: `!buy [Fief Name] [Unit Type] [Amount]`")

        # 1. Parse Arguments manually from the right side
        # args = "Storms End Infantry 2000" -> ["Storms End", "Infantry", "2000"]
        try:
            parts = args.rsplit(maxsplit=2)
            if len(parts) != 3:
                raise ValueError

            fief_name_raw, unit_type_raw, amount_raw = parts

            if not amount_raw.isdigit():
                return await ctx.send(
                    f"❌ Amount must be a number, not '{amount_raw}'."
                )

            amount = int(amount_raw)
            unit_type = unit_type_raw.lower()
            fief_name = fief_name_raw.strip()

        except ValueError:
            return await ctx.send(
                "❌ Usage: `!buy [Fief Name] [Unit Type] [Amount]`\nExample: `!buy Storm's End Infantry 100`"
            )

        # 2. Logic Validation
        if amount <= 0:
            return await ctx.send("❌ Amount must be greater than zero.")
        if unit_type not in VALID_UNITS:
            return await ctx.send(
                f"❌ Invalid unit type: `{unit_type_raw}`.\nMust be one of: `{', '.join(VALID_UNITS)}`"
            )

        async with get_session() as session:
            # 3. Setup Context
            player_house = await self._get_player_house(session, ctx)
            if not player_house:
                return await ctx.send("❌ You do not have a house in this game.")

            game = await GameRepo.get_active_game(session, ctx.guild.id)
            game_id = game.game_id if game else None

            # 4. Find Fief
            fief = await session.scalar(
                select(Fief).where(Fief.game_id == game_id, Fief.name.ilike(fief_name))
            )
            if not fief:
                return await ctx.send(
                    f"❌ Fief named `{fief_name}` not found.\n*Check spelling and apostrophes (e.g., Storm's End)*"
                )
            if fief.owner_id != player_house.house_id:
                return await ctx.send(f"❌ You do not own {fief.name}.")

            # 5. Calculate Costs
            gold_cost = amount * UNIT_PRICES[unit_type]["buy"]
            manpower_cost = amount * UNIT_PRICES[unit_type]["manpower_cost"]

            # 6. Validate Funds (LOCAL FIEF TREASURY) & Manpower (GLOBAL HOUSE POOL)
            current_fief_gold = fief.treasury or 0

            if current_fief_gold < gold_cost:
                return await ctx.send(
                    f"❌ Insufficient funds in **{fief.name}**.\n"
                    f"Required: {gold_cost} Gold | Available Locally: {current_fief_gold} Gold"
                )

            if manpower_cost > 0 and player_house.manpower < manpower_cost:
                return await ctx.send(
                    f"❌ Insufficient Manpower.\n"
                    f"Required: {manpower_cost} | Available: {player_house.manpower}"
                )

            # 7. Find or Create Garrison/Fleet
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

            # 8. Execute Transaction
            fief.treasury -= gold_cost  # Deduct from FIEF
            if manpower_cost > 0:
                player_house.manpower -= manpower_cost  # Deduct from HOUSE

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
            embed.add_field(
                name="Gold Cost",
                value=f"{gold_cost} 💰 (Paid from {fief.name})",
                inline=True,
            )
            if manpower_cost > 0:
                embed.add_field(
                    name="Manpower Used", value=f"{manpower_cost} recruits", inline=True
                )
            embed.set_footer(
                text=f"{fief.name} Treasury: {fief.treasury} Gold | Global Manpower: {player_house.manpower}"
            )
            await ctx.send(embed=embed)

    @commands.command()
    async def crown_transfer(self, ctx, target: discord.Member, amount: int):
        """Master of Coin: Transfer money from the Iron Throne (King's Landing Vault) to a house."""
        moc_role = discord.utils.get(ctx.guild.roles, name="Master of Coin")
        if not (
            ctx.author.guild_permissions.administrator
            or (moc_role and moc_role in ctx.author.roles)
        ):
            return await ctx.send("❌ You are not the Master of Coin.")

        if amount <= 0:
            return await ctx.send("❌ Amount must be positive.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Find Royal Treasury (The FIEF of King's Landing)
            # We look for the physical location "King's Landing"
            stmt_kl = select(Fief).where(
                Fief.name == "King's Landing", Fief.game_id == game.game_id
            )
            kings_landing = (await session.execute(stmt_kl)).scalars().first()

            if not kings_landing:
                return await ctx.send(
                    "❌ Critical Error: Fief 'King's Landing' not found."
                )

            # Check Local Vault
            current_royal_gold = kings_landing.treasury or 0
            if current_royal_gold < amount:
                return await ctx.send(
                    f"❌ Royal Treasury (King's Landing) insufficient.\n"
                    f"Available: {current_royal_gold} Gold"
                )

            # 2. Find Target and their Lands
            stmt_target = (
                select(GamePlayer)
                .join(User)
                .where(User.discord_id == target.id, GamePlayer.game_id == game.game_id)
                .options(selectinload(GamePlayer.house).selectinload(House.fiefs))
            )
            target_player = (await session.execute(stmt_target)).scalars().first()
            if not target_player or not target_player.house:
                return await ctx.send(f"❌ {target.mention} does not control a House.")

            # Determine where to put the money (The Target's Capital)
            # We default to the first fief in their list.
            target_house = target_player.house
            if not target_house.fiefs:
                return await ctx.send(
                    f"❌ **{target_house.name}** holds no lands to store this gold."
                )

            # Usually the first fief is the primary seat
            target_fief = target_house.fiefs[0]

            # 3. Execute Transfer
            kings_landing.treasury -= amount
            target_fief.treasury = (target_fief.treasury or 0) + amount

            await session.commit()

            # 4. Notify in Private Quarters via ID
            if target_player.private_channel_id:
                chan = self.bot.get_channel(target_player.private_channel_id)
                if chan:
                    embed = discord.Embed(
                        title="💰 Royal Grant", color=discord.Color.gold()
                    )
                    embed.description = f"The Master of Coin has transferred **{amount} Gold** from the Iron Throne to your vaults at **{target_fief.name}**."
                    await chan.send(content=target.mention, embed=embed)

            await ctx.send(
                f"✅ Transferred **{amount} gold** from King's Landing to **{target_player.house.name}** (stored at {target_fief.name})."
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
            if report_pages and "CRITICAL DATA ERROR" in report_pages[0]:
                await status_msg.delete()
                await ctx.send(f"⚠️ **Aborted:** {report_pages[0]}")
                # We do NOT increment the year, and we do NOT commit.
                return
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
    async def loot(self, ctx, amount: int, target_name: str, looter_name: str):
        """
        Transfers gold + announces pillage.
        Target can be a Fief Name (recommended) or a House Name.
        Usage: !loot 5000 "Winterfell" "Greyjoy"
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Find Looter House
            stmt_looter = select(House).where(
                House.name.ilike(looter_name), House.game_id == game.game_id
            )
            looter_house = (await session.execute(stmt_looter)).scalars().first()
            if not looter_house:
                return await ctx.send(f"❌ Looter House '{looter_name}' not found.")

            # 2. Identify Target (Fief or House)

            # A. Try Fief First (Preferred for Localized Economy)
            stmt_fief = (
                select(Fief)
                .where(Fief.name.ilike(target_name), Fief.game_id == game.game_id)
                .options(selectinload(Fief.owner))
            )
            target_fief = (await session.execute(stmt_fief)).scalars().first()

            victim_house_name = "Unknown"
            source_description = ""

            if target_fief:
                # It's a Fief - Check Fief Treasury
                current_gold = target_fief.treasury or 0
                if current_gold < amount:
                    return await ctx.send(
                        f"❌ **{target_fief.name}** only has {current_gold} Gold."
                    )

                target_fief.treasury -= amount
                victim_house_name = (
                    target_fief.owner.name if target_fief.owner else "Independent"
                )
                source_description = f"the vaults of **{target_fief.name}**"

            else:
                # B. Try House (Fallback)
                stmt_house = select(House).where(
                    House.name.ilike(target_name), House.game_id == game.game_id
                )
                target_house = (await session.execute(stmt_house)).scalars().first()

                if not target_house:
                    return await ctx.send(
                        f"❌ Target '{target_name}' not found (neither Fief nor House)."
                    )

                current_gold = target_house.treasury or 0
                if current_gold < amount:
                    return await ctx.send(
                        f"❌ **{target_house.name}** central treasury only has {current_gold} Gold."
                    )

                target_house.treasury -= amount
                victim_house_name = target_house.name
                source_description = f"the central treasury of **{target_house.name}**"

            # 3. Execute Transfer
            looter_house.treasury += amount
            await session.commit()

            # 4. Announce
            news_chan = discord.utils.get(
                ctx.guild.text_channels, name="news-and-events"
            )
            embed = discord.Embed(
                title="🔥 City Sacked!", color=discord.Color.dark_red()
            )
            embed.description = f"**House {looter_house.name}** has raided {source_description} (House {victim_house_name})!"
            embed.add_field(name="Loot Taken", value=f"💰 {amount} Gold Dragons")

            if news_chan:
                await news_chan.send(embed=embed)
            await ctx.send(f"✅ Loot successfully transferred to {looter_house.name}.")

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

    @commands.group(name="gold", invoke_without_command=True)
    async def gold(self, ctx):
        """Player gold command hub."""
        await ctx.send(self._gold_help_text(is_gm=False))

    @gold.command(name="send")
    async def gold_send(self, ctx, amount: int, *, route: str):
        """
        Unified player transfer command.
        Usage: !gold send 500 from fief Winterfell to army 123
        """
        if not await self._ensure_money_channel(ctx):
            return

        if amount <= 0:
            return await ctx.send("Error: Amount must be positive.")

        source_text, target_text = self._split_gold_route(route)
        if not source_text or not target_text:
            return await ctx.send(
                "Error: Use `!gold send <amount> from <source> to <target>`.\n"
                "Example: `!gold send 500 from fief Winterfell to army 123`"
            )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("Error: No active game.")

            player_house = await self._get_player_house(session, ctx)
            if not player_house:
                return await ctx.send("Error: You do not command a House.")

            source, err = await self._resolve_money_endpoint(
                session, game, source_text, player_house=player_house
            )
            if err:
                return await ctx.send(f"Error: {err}")

            if self._endpoint_owner_id(source) != player_house.house_id:
                return await ctx.send(
                    f"Error: You do not control the source: **{self._endpoint_label(source)}**."
                )

            target, err = await self._resolve_money_endpoint(
                session,
                game,
                target_text,
                player_house=player_house,
                source_endpoint=source,
            )
            if err:
                return await ctx.send(f"Error: {err}")

            service = EconomyService(session)
            success, msg = await service.execute_transfer(
                amount=amount,
                source_type=source["type"],
                source_id=source["id"],
                target_type=target["type"],
                target_id=target["id"],
            )
            await ctx.send(msg if success else f"Error: {msg}")

    @gold.command(name="check")
    async def gold_check(self, ctx, *, target: str = None):
        """
        Checks one player-accessible money pocket.
        Usage: !gold check fief Winterfell
        """
        if not await self._ensure_money_channel(ctx):
            return

        if not target:
            return await ctx.send(
                "Use `!gold check <source>`, such as `!gold check fief Winterfell`."
            )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("Error: No active game.")

            player_house = await self._get_player_house(session, ctx)
            if not player_house:
                return await ctx.send("Error: You do not command a House.")

            endpoint, err = await self._resolve_money_endpoint(
                session, game, target, player_house=player_house
            )
            if err:
                return await ctx.send(f"Error: {err}")
            if self._endpoint_owner_id(endpoint) != player_house.house_id:
                return await ctx.send(
                    f"Error: You do not control **{self._endpoint_label(endpoint)}**."
                )

            balance = endpoint["obj"].treasury or 0
            await ctx.send(
                f"Gold: **{self._endpoint_label(endpoint)}**: **{balance:,} Gold**"
            )

    @commands.command(name="transfer_gold")
    @commands.check(is_in_house_channel)
    async def transfer_gold(
        self, ctx, amount: int, target_type: str, *, identifier: str
    ):
        """
        Send gold FROM your Capital Fief.
        Usage: !transfer_gold 500 army 123
        """
        await self._generic_player_transfer(ctx, None, amount, target_type, identifier)

    @commands.command(name="transfer_from_fief")
    @commands.check(is_in_house_channel)
    async def transfer_from_fief(
        self,
        ctx,
        source_fief_name: str,
        amount: int,
        target_type: str,
        *,
        identifier: str,
    ):
        """
        Send gold FROM a specific Fief you own.
        Usage: !transfer_from_fief "Harrenhal" 500 army 123
        Usage: !transfer_from_fief "Winterfell" 1000 fief "Deepwood Motte"
        """
        await self._generic_player_transfer(
            ctx, source_fief_name, amount, target_type, identifier
        )

    async def _generic_player_transfer(
        self, ctx, source_fief_name, amount, target_type, identifier
    ):
        """Handles logic for players transferring gold."""
        target_type = target_type.upper()
        if target_type == "FLEET":
            target_type = "ARMY"
        if target_type not in ["ARMY", "FIEF", "HOUSE"]:
            return await ctx.send("❌ Target type must be `army`, `fief`, or `house`.")

        if amount <= 0:
            return await ctx.send("❌ Amount must be positive.")

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Validate Owner
            player_house = await self._get_player_house(session, ctx)
            if not player_house:
                return await ctx.send("❌ No House.")

            # 2. Determine Source
            source_id = 0

            if source_fief_name:
                # Specific Fief
                stmt = select(Fief).where(
                    Fief.game_id == game.game_id, Fief.name.ilike(source_fief_name)
                )
                fief = (await session.execute(stmt)).scalars().first()
                if not fief or fief.owner_id != player_house.house_id:
                    return await ctx.send(
                        f"❌ You do not own a fief named '{source_fief_name}'."
                    )
                source_id = fief.fief_id
            else:
                # Capital (Default)
                if not player_house.fiefs:
                    return await ctx.send("❌ You have no lands.")
                # Assumes first fief is capital. Better: add is_capital bool to Fief model later.
                source_id = player_house.fiefs[0].fief_id

            # 3. Resolve Target
            target_id = 0
            target_owner_id = -1
            service = EconomyService(session)

            if target_type == "ARMY":
                if not identifier.isdigit():
                    return await ctx.send("❌ Army ID must be a number.")
                army, _, _ = await service.get_army_gold(int(identifier))
                if not army:
                    return await ctx.send("❌ Army not found.")
                target_id = army.army_id
                target_owner_id = army.house_id

            elif target_type == "FIEF":
                stmt = select(Fief).where(
                    Fief.game_id == game.game_id, Fief.name.ilike(identifier)
                )
                fief = (await session.execute(stmt)).scalars().first()
                if not fief:
                    return await ctx.send(f"❌ Fief '{identifier}' not found.")
                target_id = fief.fief_id
                target_owner_id = fief.owner_id

            elif target_type == "HOUSE":
                stmt = select(House).where(
                    House.game_id == game.game_id, House.name.ilike(identifier)
                )
                house = (await session.execute(stmt)).scalars().first()
                if not house:
                    return await ctx.send(f"❌ House '{identifier}' not found.")
                target_id = (
                    house.house_id
                )  # Service converts House ID -> Capital Fief ID
                target_owner_id = house.house_id

            # 4. Execute
            # If sending to self, execute immediately. If external, use View (simplified here to just execute for now)
            # You can add the TransactionView logic back here if you want approvals.

            success, msg = await service.execute_transfer(
                amount=amount,
                source_type="FIEF",
                source_id=source_id,
                target_type=target_type,
                target_id=target_id,
            )
            await ctx.send(msg if success else f"❌ {msg}")

    @commands.command(name="deposit_gold")
    @commands.check(is_in_house_channel)
    async def deposit_gold(self, ctx, amount: int, army_id: int):
        """Transfers gold FROM an Army TO the local Fief."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            player_house = await self._get_player_house(session, ctx)
            if not player_house:
                return await ctx.send("❌ No House.")

            army = await session.get(Army, army_id)
            if not army or army.house_id != player_house.house_id:
                return await ctx.send("❌ You do not own that army.")

            stmt = select(Fief).where(
                Fief.game_id == game.game_id,
                Fief.location_x == army.location_x,
                Fief.location_y == army.location_y,
            )
            fief = (await session.execute(stmt)).scalars().first()
            if not fief:
                return await ctx.send("❌ Army is not at a fief.")

            service = EconomyService(session)
            success, msg = await service.execute_transfer(
                amount=amount,
                source_type="ARMY",
                source_id=army.army_id,
                target_type="FIEF",
                target_id=fief.fief_id,
            )
            await ctx.send(msg if success else f"❌ {msg}")

    @commands.command(name="deposit_gold")
    @commands.check(is_in_house_channel)
    async def deposit_gold(self, ctx, amount: int, army_id: int):
        """
        Transfers gold FROM an Army TO the local Fief (Army must be at the Fief).
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            player_house = await self._get_player_house(session, ctx)
            if not player_house:
                return await ctx.send("❌ No House.")

            # Get Army
            army = await session.get(Army, army_id)
            if not army or army.house_id != player_house.house_id:
                return await ctx.send("❌ Army not found or not owned.")

            # Find Fief at location
            stmt_fief = select(Fief).where(
                Fief.game_id == game.game_id,
                Fief.location_x == army.location_x,
                Fief.location_y == army.location_y,
            )
            fief = (await session.execute(stmt_fief)).scalars().first()

            if not fief:
                return await ctx.send(
                    f"❌ **{army.commander_name}** is not at a registered Fief."
                )

            # Using the new generic service
            service = EconomyService(session)

            # Note: We allow depositing into ANY fief (even not owned) or restrict to OWNED?
            # User prompt implied "Between fief to other player fief", so depositing into an ally's fief is valid.

            success, msg = await service.execute_transfer(
                amount=amount,
                source_type="ARMY",
                source_id=army.army_id,
                target_type="FIEF",
                target_id=fief.fief_id,
            )

            await ctx.send(msg if success else f"❌ {msg}")

    # --- 3. GM ECONOMY COMMANDS ---

    @commands.group(name="gm_gold", invoke_without_command=True)
    @commands.check(is_gm)
    async def gm_gold(self, ctx):
        """GM gold command hub."""
        await ctx.send(self._gold_help_text(is_gm=True))

    @gm_gold.command(name="send")
    async def gm_gold_send(self, ctx, amount: int, *, route: str):
        """
        Unified GM transfer command.
        Usage: !gm_gold send 500 from fief Winterfell to army 123
        """
        if amount <= 0:
            return await ctx.send("Error: Amount must be positive.")

        source_text, target_text = self._split_gold_route(route)
        if not source_text or not target_text:
            return await ctx.send(
                "Error: Use `!gm_gold send <amount> from <source> to <target>`.\n"
                "Example: `!gm_gold send 500 from fief Winterfell to army 123`"
            )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("Error: No active game.")

            source, err = await self._resolve_money_endpoint(
                session, game, source_text
            )
            if err:
                return await ctx.send(f"Error: {err}")

            target, err = await self._resolve_money_endpoint(
                session, game, target_text, source_endpoint=source
            )
            if err:
                return await ctx.send(f"Error: {err}")

            service = EconomyService(session)
            success, msg = await service.execute_transfer(
                amount=amount,
                source_type=source["type"],
                source_id=source["id"],
                target_type=target["type"],
                target_id=target["id"],
            )

            color = discord.Color.green() if success else discord.Color.red()
            embed = discord.Embed(title="GM Gold Transfer", description=msg, color=color)
            await ctx.send(embed=embed)

    @gm_gold.command(name="check")
    async def gm_gold_check(self, ctx, *, target: str):
        """
        Checks one money pocket by name or ID.
        Usage: !gm_gold check fief Winterfell
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("Error: No active game.")

            endpoint, err = await self._resolve_money_endpoint(session, game, target)
            if err:
                return await ctx.send(f"Error: {err}")

            balance = endpoint["obj"].treasury or 0
            await ctx.send(
                f"Gold: **{self._endpoint_label(endpoint)}**: **{balance:,} Gold**"
            )

    @gm_gold.command(name="audit", aliases=["economy", "bal", "balance"])
    async def gm_gold_audit(self, ctx, *, house_identifier: str):
        """
        Alias for the full GM house ledger.
        Usage: !gm_gold audit Stark
        """
        command = self.bot.get_command("gm_econ economy")
        if not command:
            return await ctx.send("Error: GM economy audit command is unavailable.")
        await ctx.invoke(command, house_identifier=house_identifier)

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
            "`!gm_econ stop_tax [House]` - Toggle tax statu\ns"
            "`!gm_econ set_income [type] [target] [value]`\n"
            "`!gm_econ list_income`\n"
        )

    @gm_econ.command(name="check")
    async def gm_check(self, ctx, *, identifier: str):
        """GM Audit: Checks gold for a House (name/ID) or Army (ID or House + Army ID)."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            parts = identifier.split()

            # --------------------------------------------------
            # CASE 1: House + Army ID  (e.g. "Stark 580" or "3 580")
            # --------------------------------------------------
            if len(parts) == 2 and parts[1].isdigit():
                house_ident, army_id = parts

                stmt_house = select(House).where(House.game_id == game.game_id)
                if house_ident.isdigit():
                    stmt_house = stmt_house.where(House.house_id == int(house_ident))
                else:
                    stmt_house = stmt_house.where(House.name.ilike(house_ident))

                house = (await session.execute(stmt_house)).scalars().first()
                if not house:
                    return await ctx.send("❌ House not found.")

                stmt_army = select(Army).where(
                    Army.army_id == int(army_id),
                    Army.house_id == house.house_id,
                )
                army = (await session.execute(stmt_army)).scalars().first()
                if not army:
                    return await ctx.send("❌ Army not found for that House.")

                service = EconomyService(session)
                _, gold, name = await service.get_army_gold(army.army_id)

                return await ctx.send(
                    f"🕵️ **GM Audit:** Army **{name}** (ID: {army.army_id})\n"
                    f"House: **{house.name}**\n"
                    f"Treasury: **{gold} Gold**"
                )

            # --------------------------------------------------
            # CASE 2: House only (name or ID)
            # --------------------------------------------------
            stmt_house = select(House).where(House.game_id == game.game_id)
            if identifier.isdigit():
                stmt_house = stmt_house.where(House.house_id == int(identifier))
            else:
                stmt_house = stmt_house.where(House.name.ilike(identifier))

            house = (await session.execute(stmt_house)).scalars().first()
            if house:
                return await ctx.send(
                    f"🕵️ **GM Audit:** House **{house.name}** (ID: {house.house_id})\n"
                    f"Treasury: **{house.treasury} Gold**"
                )

            # --------------------------------------------------
            # CASE 3: Army only (ID)
            # --------------------------------------------------
            if identifier.isdigit():
                service = EconomyService(session)
                army, gold, name = await service.get_army_gold(int(identifier))
                if army:
                    owner = army.house.name if army.house else "Unknown"
                    return await ctx.send(
                        f"🕵️ **GM Audit:** Army **{name}** (ID: {army.army_id})\n"
                        f"Owner: {owner}\n"
                        f"Treasury: **{gold} Gold**"
                    )

            await ctx.send(f"❌ Could not find House or Army matching '{identifier}'.")

    @gm_econ.command(name="transfer")
    async def gm_transfer(
        self,
        ctx,
        amount: int,
        source_type: str,
        source_id: int,
        target_type: str,
        target_id: int,
    ):
        """
        GM: Force transfer between ANY entities.
        Types: ARMY, FIEF, HOUSE (House implies Capital).
        IDs: The numeric database ID of the entity.

        Usage Examples:
        !gm_econ transfer 100 FIEF 20 ARMY 55  (Fief 20 -> Army 55)
        !gm_econ transfer 500 HOUSE 1 HOUSE 2  (Stark Capital -> Lannister Capital)
        !gm_econ transfer 50 ARMY 99 FIEF 10   (Army 99 -> Fief 10)
        """
        source_type = source_type.upper()
        target_type = target_type.upper()

        valid_types = ["ARMY", "FIEF", "HOUSE"]
        if source_type not in valid_types or target_type not in valid_types:
            return await ctx.send(f"❌ Types must be: {', '.join(valid_types)}")

        async with get_session() as session:
            service = EconomyService(session)
            success, msg = await service.execute_transfer(
                amount=amount,
                source_type=source_type,
                source_id=source_id,
                target_type=target_type,
                target_id=target_id,
            )

            color = discord.Color.green() if success else discord.Color.red()
            embed = discord.Embed(title="👮 GM Transfer", description=msg, color=color)
            await ctx.send(embed=embed)

    @gm_econ.command(name="deposit")
    async def gm_deposit(
        self, ctx, amount: int, army_id: int, target_house_id: int = None
    ):
        """
        GM: Force transfer gold FROM an Army TO a House's Capital Fief.
        If target_house_id is blank, goes to the army's owner.
        Usage: !gm_econ deposit 500 [ArmyID] (Optional: [TargetHouseID])
        """
        if amount <= 0:
            return await ctx.send("❌ Amount must be positive.")

        async with get_session() as session:
            # 1. Fetch Army
            army = await session.get(Army, army_id)
            if not army:
                return await ctx.send(f"❌ Army ID {army_id} not found.")

            # 2. Determine Destination House
            dest_house_id = target_house_id if target_house_id else army.house_id
            destination_house = await session.get(House, dest_house_id)

            if not destination_house:
                return await ctx.send(
                    f"❌ Destination House ID {dest_house_id} not found."
                )

            # 3. Find Destination Capital (Where the gold actually goes)
            stmt_fief = (
                select(Fief)
                .where(Fief.owner_id == dest_house_id)
                .order_by(Fief.fief_id.asc())
                .limit(1)
            )
            capital_fief = (await session.execute(stmt_fief)).scalars().first()

            if not capital_fief:
                return await ctx.send(
                    f"❌ House **{destination_house.name}** has no fiefs to store gold."
                )

            # 4. Validate Funds
            current_gold = army.treasury or 0
            if current_gold < amount:
                return await ctx.send(f"❌ Army only has {current_gold} gold.")

            # 5. Execute Transfer
            army.treasury -= amount
            capital_fief.treasury = (capital_fief.treasury or 0) + amount

            await session.commit()

            embed = discord.Embed(
                title="💰 GM Deposit Executed",
                description=f"Transferred **{amount} Gold** from **{army.commander_name}** to **{destination_house.name}** (stored at **{capital_fief.name}**).",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Army Remaining", value=f"{army.treasury}", inline=True
            )
            embed.add_field(
                name="Capital Total", value=f"{capital_fief.treasury}", inline=True
            )

            await ctx.send(embed=embed)

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

    @gm_econ.command(name="set_income")
    async def gm_set_income(
        self, ctx, mod_type: str, target: str, *, value: str = None
    ):
        """
        Sets an income modifier for the next !year_end.

        Usage:
        !gm_eco set_income global half
        !gm_eco set_income region "The North" 50%
        !gm_eco set_income house "Stark" 0%
        !gm_eco set_income region "The North" reset
        """
        mod_type = mod_type.lower()

        # --- FIX: Handle the 'global' case ---
        # If the type is global, the user likely typed `!gm_eco set_income global half`
        # In this case, the `target` variable will contain "half", and `value` will be None.
        if mod_type == "global":
            if value is None:
                value = target  # Re-assign the value correctly
                target = "global"  # The service expects a placeholder target

        # For other types, a value is mandatory.
        elif value is None:
            await ctx.send(
                "❌ **Missing Value:** You must provide a value (e.g., 'half', '50%', 'reset').\n`!gm_eco set_income region \"The North\" 50%`"
            )
            return
        # ------------------------------------

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            service = EconomyService(session)
            success, msg = await service.set_income_modifier(
                game.game_id, mod_type, target, value.lower()
            )

            color = discord.Color.green() if success else discord.Color.red()
            await ctx.send(embed=discord.Embed(description=msg, color=color))

    @gm_econ.command(name="list_income")
    async def gm_list_income(self, ctx):
        """Shows all active income modifiers."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return

            mods = game.income_modifiers or {}
            embed = discord.Embed(
                title="Active Income Modifiers", color=discord.Color.blue()
            )

            embed.add_field(
                name="Global",
                value=f"{mods.get('global', 1.0) * 100:.0f}%",
                inline=False,
            )

            region_str = (
                "\n".join(
                    [f"- {r}: {v*100:.0f}%" for r, v in mods.get("regions", {}).items()]
                )
                or "None"
            )
            embed.add_field(name="Regions", value=region_str, inline=False)

            house_mods = mods.get("houses", {})
            if house_mods:
                house_ids = [int(h_id) for h_id in house_mods.keys()]
                stmt = select(House.house_id, House.name).where(
                    House.house_id.in_(house_ids)
                )
                house_map = {h.house_id: h.name for h in await session.execute(stmt)}
                house_str = "\n".join(
                    [
                        f"- {house_map.get(int(h_id), h_id)}: {v*100:.0f}%"
                        for h_id, v in house_mods.items()
                    ]
                )
            else:
                house_str = "None"

            embed.add_field(name="Specific Houses", value=house_str, inline=False)
            await ctx.send(embed=embed)

    @gm_econ.command(name="sell")
    async def gm_sell(
        self, ctx, house_identifier: str, army_id: int, unit_type: str, amount: int
    ):
        """
        GM: Sell units on behalf of a House (Refunds Gold/Manpower).
        Usage: !gm_econ sell Stark 123 infantry 500
        """
        unit_type = unit_type.lower()
        if amount <= 0:
            return await ctx.send("❌ Amount must be positive.")
        if unit_type not in VALID_UNITS:
            return await ctx.send(
                f"❌ Invalid unit type. Options: `{', '.join(VALID_UNITS)}`"
            )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Find House
            house = await HouseRepo.get_house_by_name_or_id(session, house_identifier)
            if not house or house.game_id != game.game_id:
                return await ctx.send(f"❌ House '{house_identifier}' not found.")

            # 2. Find Army
            army = await session.get(Army, army_id)
            if not army:
                return await ctx.send(f"❌ Army ID {army_id} not found.")

            # 3. Ownership Check
            if army.house_id != house.house_id:
                return await ctx.send(
                    f"❌ Army {army_id} does not belong to {house.name}."
                )

            # 4. Cargo Validation (Prevent selling loaded ships)
            if (
                unit_type == "ships"
                and army.cargo
                and (
                    army.cargo.get("troop_count", 0) > 0 or army.cargo.get("prisoners")
                )
            ):
                return await ctx.send(
                    "❌ Cannot force-sell ships that are carrying troops/prisoners. Unload them first."
                )

            # 5. Check Inventory
            current_amt = army.composition.get(unit_type, 0)
            if current_amt < amount:
                return await ctx.send(f"❌ Army only has {current_amt} {unit_type}.")

            # 6. Calculate Refunds
            prices = UNIT_PRICES[unit_type]
            gold_refund = amount * prices["sell"]
            manpower_refund = amount * prices["manpower_cost"]

            # 7. Localized Economy Logic: Where does the gold go?
            gold_dest_name = ""
            existing_army_gold = army.treasury or 0
            army_will_survive = (army.troop_count - amount) > 0

            # Check if at a friendly Fief
            stmt_fief = select(Fief).where(
                Fief.game_id == game.game_id,
                Fief.location_x == army.location_x,
                Fief.location_y == army.location_y,
                Fief.owner_id == house.house_id,
            )
            local_fief = (await session.execute(stmt_fief)).scalars().first()

            if local_fief:
                # SCENARIO A: At Home -> Deposit to Fief
                deposit = gold_refund
                if not army_will_survive:
                    deposit += existing_army_gold  # Recover carried gold if disbanding

                local_fief.treasury = (local_fief.treasury or 0) + deposit
                gold_dest_name = f"**{local_fief.name}** Treasury"

                if not army_will_survive:
                    army.treasury = 0  # Emptied

            elif army_will_survive:
                # SCENARIO B: Field & Survives -> Keep on Army
                army.treasury = existing_army_gold + gold_refund
                gold_dest_name = f"**{army.commander_name}** Coffers"

            else:
                # SCENARIO C: Field & Disbands -> Wire to Capital
                stmt_cap = (
                    select(Fief)
                    .where(Fief.owner_id == house.house_id)
                    .order_by(Fief.fief_id.asc())
                    .limit(1)
                )
                capital = (await session.execute(stmt_cap)).scalars().first()

                total_salvage = gold_refund + existing_army_gold

                if capital:
                    capital.treasury = (capital.treasury or 0) + total_salvage
                    gold_dest_name = f"**{capital.name}** (Capital)"
                else:
                    # Fallback if house is landless
                    house.treasury += total_salvage
                    gold_dest_name = "House Treasury (Landless)"

            # 8. Refund Manpower (Global)
            if manpower_refund > 0:
                house.manpower += manpower_refund

            # 9. Update Army Units
            army.composition[unit_type] -= amount
            if army.composition[unit_type] <= 0:
                del army.composition[unit_type]
            army.troop_count -= amount
            flag_modified(army, "composition")

            response_text = (
                f"Sold **{amount} {unit_type}** from **{army.commander_name}**.\n"
                f"💰 **Refunded:** {gold_refund} Gold -> {gold_dest_name}\n"
                f"🛡️ **Manpower:** +{manpower_refund}"
            )

            # Delete if empty
            if army.troop_count <= 0:
                await session.delete(army)
                response_text += "\n⚠️ Army disbanded (0 troops)."

            await session.commit()

            embed = discord.Embed(
                description=f"✅ **GM Transaction:** {response_text}",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)

    @gm_econ.command(name="buy")
    async def gm_buy(self, ctx, *, args: str = None):
        """
        GM: Recruit units for a House (Deducts Gold/Manpower unless 'free').
        Usage:
        !gm_econ buy [House] [Fief Name] [Unit] [Amount] (free)
        Example: !gm_econ buy Stark Winterfell infantry 100
        """
        if not args:
            return await ctx.send(
                "❌ Usage: `!gm_econ buy [House] [Fief] [Unit] [Amount] (free)`"
            )

        # Parsing Logic
        parts = args.split()

        # Check for flags (free)
        is_free = False
        if parts and parts[-1].lower() == "free":
            is_free = True
            parts.pop()

        if len(parts) < 4:
            return await ctx.send(
                "❌ Usage: `!gm_econ buy [House] [Fief] [Unit] [Amount] (free)`"
            )

        # Extract strict fields from edges
        amount_raw = parts.pop()
        unit_type_raw = parts.pop()

        # House is first
        house_identifier = parts.pop(0)

        # Fief is whatever is left in the middle
        fief_name = " ".join(parts)

        if not fief_name:
            return await ctx.send("❌ Fief name missing.")

        # Validation
        if not amount_raw.isdigit():
            return await ctx.send("❌ Amount must be a positive integer.")
        amount = int(amount_raw)

        unit_type = unit_type_raw.lower()
        if amount <= 0:
            return await ctx.send("❌ Amount must be positive.")
        if unit_type not in VALID_UNITS:
            return await ctx.send(
                f"❌ Invalid unit type. Options: `{', '.join(VALID_UNITS)}`"
            )

        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Find House
            house = await HouseRepo.get_house_by_name_or_id(session, house_identifier)
            if not house or house.game_id != game.game_id:
                return await ctx.send(f"❌ House '{house_identifier}' not found.")

            # 2. Find Fief
            stmt_fief = select(Fief).where(
                Fief.game_id == game.game_id, Fief.name.ilike(fief_name)
            )
            fief = (await session.execute(stmt_fief)).scalars().first()
            if not fief:
                return await ctx.send(f"❌ Fief '{fief_name}' not found.")

            # 3. Ownership Check (Required for Localized Economy)
            if fief.owner_id != house.house_id:
                return await ctx.send(
                    f"❌ **{house.name}** does not own **{fief.name}**. Cannot access local treasury."
                )

            # 4. Calculate Costs
            prices = UNIT_PRICES[unit_type]
            gold_cost = amount * prices["buy"]
            manpower_cost = amount * prices["manpower_cost"]

            # 5. Process Costs (Unless Free)
            cost_msg = "**(Free - GM Override)**"
            current_fief_gold = fief.treasury or 0

            if not is_free:
                # Validate Funds (Local Fief)
                if current_fief_gold < gold_cost:
                    return await ctx.send(
                        f"❌ **Insufficient Local Funds:** **{fief.name}** has {current_fief_gold} Gold, needs {gold_cost}.\n"
                        f"Use `... {amount} free` to bypass."
                    )
                # Validate Manpower (Global House)
                if house.manpower < manpower_cost:
                    return await ctx.send(
                        f"❌ **Insufficient Manpower:** {house.name} has {house.manpower}, needs {manpower_cost}.\n"
                        f"Use `... {amount} free` to bypass."
                    )

                # Deduct
                fief.treasury -= gold_cost
                house.manpower -= manpower_cost
                cost_msg = f"(Cost: {gold_cost} Gold from {fief.name}, {manpower_cost} Manpower)"

            # 6. Find or Create Army
            army_type = "SEA" if unit_type == "ships" else "LAND"
            stmt_army = select(Army).where(
                Army.house_id == house.house_id,
                Army.location_x == fief.location_x,
                Army.location_y == fief.location_y,
                Army.status.in_(["GARRISONED", "DOCKED", "IDLE"]),
                Army.army_type == army_type,
            )
            garrison = (await session.execute(stmt_army)).scalars().first()

            if not garrison:
                garrison_status = "DOCKED" if army_type == "SEA" else "GARRISONED"
                garrison_name = (
                    f"Fleet of {fief.name}"
                    if army_type == "SEA"
                    else f"Garrison of {fief.name}"
                )

                garrison = Army(
                    game_id=game.game_id,
                    house_id=house.house_id,
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

            # 7. Add Units
            garrison.troop_count += amount
            garrison.composition[unit_type] = (
                garrison.composition.get(unit_type, 0) + amount
            )
            flag_modified(garrison, "composition")

            await session.commit()

            embed = discord.Embed(
                title="✅ GM Recruitment",
                description=f"Recruited **{amount} {unit_type}** for **{house.name}** at **{fief.name}**.\n{cost_msg}",
                color=discord.Color.green(),
            )
            embed.set_footer(
                text=f"{fief.name} Treasury: {fief.treasury} | House Manpower: {house.manpower}"
            )
            await ctx.send(embed=embed)

    @gm_econ.command(name="fief")
    async def gm_fief_lookup(self, ctx, *, fief_name: str):
        """
        GM: Search for Fief IDs by name.
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # CRITICAL: options(selectinload(Fief.owner)) prevents MissingGreenlet error
            stmt = (
                select(Fief)
                .where(Fief.game_id == game.game_id, Fief.name.ilike(f"%{fief_name}%"))
                .options(selectinload(Fief.owner))
                .order_by(Fief.name)
            )

            fiefs = (await session.execute(stmt)).scalars().all()

            if not fiefs:
                return await ctx.send(f"❌ No fiefs found matching '**{fief_name}**'.")

            lines = []
            for f in fiefs:
                # f.owner access here is now safe because of selectinload above
                owner = f.owner.name if f.owner else "Independent"
                lines.append(
                    f"🏰 **{f.name}** (ID: `{f.fief_id}`) | Owner: {owner} | 💰 {f.treasury or 0}g"
                )

            await ctx.send("\n".join(lines)[:2000])

    @commands.command(name="economy", aliases=["bal", "balance", "bank"])
    @commands.check(is_in_house_channel)
    async def economy(self, ctx):
        """
        Shows a full breakdown of your House's assets and income.
        """
        async with get_session() as session:
            # 1. Get Player House
            player_house = await self._get_player_house(session, ctx)
            if not player_house:
                return await ctx.send("❌ You do not command a House.")

            # 2. Fetch Data via Service
            service = EconomyService(session)
            house, fief_total, army_total, tax_income = (
                await service.get_economy_overview(player_house.house_id)
            )

            # 3. Build Embed
            total_liquid = (house.treasury or 0) + fief_total + army_total

            embed = discord.Embed(
                title=f"💰 House {house.name} Ledger",
                description=f"**Grand Total Wealth:** `{total_liquid:,} Gold`",
                color=discord.Color.gold(),
            )

            # --- A. CENTRAL TREASURY ---
            embed.add_field(
                name="🏛️ House Treasury",
                value=f"**{house.treasury or 0:,} Gold**\n*(Safe storage)*",
                inline=False,
            )

            # --- B. FIEFS BREAKDOWN ---
            fief_lines = []
            # Sort rich to poor
            sorted_fiefs = sorted(
                house.fiefs, key=lambda x: x.treasury or 0, reverse=True
            )

            for f in sorted_fiefs:
                if f.treasury and f.treasury > 0:
                    fief_lines.append(f"**{f.name}**: {f.treasury:,} g")
                else:
                    fief_lines.append(f"{f.name}: 0")

            # Formatting list to prevent embed overflow
            fief_str = "\n".join(fief_lines)
            if len(fief_str) > 1000:
                fief_str = fief_str[:900] + "\n... (and others)"
            if not fief_str:
                fief_str = "No lands."

            embed.add_field(
                name=f"🏰 Fief Vaults (Total: {fief_total:,} Gold)",
                value=f">>> {fief_str}",
                inline=True,
            )

            # --- C. ARMIES BREAKDOWN ---
            army_lines = []
            sorted_armies = sorted(
                house.armies, key=lambda x: x.treasury or 0, reverse=True
            )

            for a in sorted_armies:
                # Only show armies that have money or troops
                if (a.treasury and a.treasury > 0) or a.troop_count > 0:
                    val = a.treasury or 0
                    army_lines.append(f"**{a.commander_name}**: {val:,} g")

            army_str = "\n".join(army_lines)
            if len(army_str) > 1000:
                army_str = army_str[:900] + "\n... (and others)"
            if not army_str:
                army_str = "No active armies."

            embed.add_field(
                name=f"⚔️ Army Coffers (Total: {army_total:,} Gold)",
                value=f">>> {army_str}",
                inline=True,
            )

            # --- D. INCOME ---
            # Note: This is projected annual tax. Fief income is internal to the fiefs.
            embed.add_field(
                name="📈 Projected Annual Tax Income",
                value=f"**{tax_income:,} Gold** / year\n*(From Vassals)*",
                inline=False,
            )

            embed.set_footer(
                text="Use !gold send <amount> from <source> to <target> to move funds."
            )

            await ctx.send(embed=embed)

    @gm_econ.command(name="economy", aliases=["bal", "balance"])
    async def gm_economy_view(self, ctx, *, house_identifier: str):
        """
        GM: View the full financial ledger of a specific House.
        Usage: !gm_econ economy Stark  OR  !gm_econ economy 12
        """
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("❌ No active game.")

            # 1. Resolve House (Name or ID)
            stmt = select(House).where(House.game_id == game.game_id)
            if house_identifier.isdigit():
                stmt = stmt.where(House.house_id == int(house_identifier))
            else:
                stmt = stmt.where(House.name.ilike(house_identifier))

            target_house = (await session.execute(stmt)).scalars().first()

            if not target_house:
                return await ctx.send(f"❌ House '{house_identifier}' not found.")

            # 2. Fetch Data via Service
            service = EconomyService(session)
            house, fief_total, army_total, tax_income = (
                await service.get_economy_overview(target_house.house_id)
            )

            # 3. Build Embed (Reused Logic)
            total_liquid = (house.treasury or 0) + fief_total + army_total

            embed = discord.Embed(
                title=f"🕵️ GM Audit: House {house.name}",
                description=f"**Grand Total Wealth:** `{total_liquid:,} Gold`",
                color=discord.Color.dark_magenta(),  # Distinct GM Color
            )

            # --- A. CENTRAL TREASURY ---
            embed.add_field(
                name="🏛️ House Treasury",
                value=f"**{house.treasury or 0:,} Gold**",
                inline=False,
            )

            # --- B. FIEFS BREAKDOWN ---
            fief_lines = []
            sorted_fiefs = sorted(
                house.fiefs, key=lambda x: x.treasury or 0, reverse=True
            )

            for f in sorted_fiefs:
                if f.treasury and f.treasury > 0:
                    fief_lines.append(f"**{f.name}**: {f.treasury:,} g")
                else:
                    fief_lines.append(f"{f.name}: 0")

            fief_str = "\n".join(fief_lines)
            if len(fief_str) > 1000:
                fief_str = fief_str[:900] + "\n... (truncated)"
            if not fief_str:
                fief_str = "No lands."

            embed.add_field(
                name=f"🏰 Fief Vaults (Total: {fief_total:,})",
                value=f">>> {fief_str}",
                inline=True,
            )

            # --- C. ARMIES BREAKDOWN ---
            army_lines = []
            sorted_armies = sorted(
                house.armies, key=lambda x: x.treasury or 0, reverse=True
            )

            for a in sorted_armies:
                if (a.treasury and a.treasury > 0) or a.troop_count > 0:
                    val = a.treasury or 0
                    army_lines.append(f"**{a.commander_name}**: {val:,} g")

            army_str = "\n".join(army_lines)
            if len(army_str) > 1000:
                army_str = army_str[:900] + "\n... (truncated)"
            if not army_str:
                army_str = "No active armies."

            embed.add_field(
                name=f"⚔️ Army Coffers (Total: {army_total:,})",
                value=f">>> {army_str}",
                inline=True,
            )

            # --- D. TAX INCOME ---
            embed.add_field(
                name="📈 Projected Tax Income",
                value=f"**{tax_income:,} Gold** / year",
                inline=False,
            )

            await ctx.send(embed=embed)

    @gm_econ.command(name="fix_sync")
    async def fix_army_sync(self, ctx):
        """
        Recalculates every army's total troop_count based on their composition.
        Fixes negative numbers and desyncs.
        """
        async with get_session() as session:
            stmt = select(Army)
            all_armies = (await session.execute(stmt)).scalars().all()

            fixed_count = 0
            for army in all_armies:
                # 1. Calculate real total from composition
                real_total = 0
                if army.composition:
                    real_total = sum(army.composition.values())

                # 2. Check for Desync
                if army.troop_count != real_total:
                    old_count = army.troop_count
                    army.troop_count = real_total
                    fixed_count += 1
                    # print(f"Fixed Army {army.army_id}: {old_count} -> {real_total}")

            await session.commit()
            await ctx.send(
                f"✅ **Synchronization Complete.** Fixed **{fixed_count}** armies with mismatched data."
            )


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
