import random
from pathlib import Path

import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.db_manager import get_session
from app.db.models import GamePlayer, House, User
from app.db.repositories import GameRepo


CARD_SUITS = ("♠", "♥", "♦", "♣")
CARD_RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
GAMBLING_ASSET_DIR = Path("assets") / "gambling"
BLACKJACK_TABLE_IMAGE = "blackjack_table.png"
BLACKJACK_WIN_IMAGE = "blackjack_win_banner.png"
GAMBLING_DEN_IMAGE = "gambling_den_banner.png"
GOLD_DRAGON_IMAGE = "gold_dragon_pile.png"
CARD_BACK_IMAGE = "card_back.png"


def new_deck() -> list[str]:
    deck = [f"{rank}{suit}" for suit in CARD_SUITS for rank in CARD_RANKS]
    random.shuffle(deck)
    return deck


def card_rank(card: str) -> str:
    return card[:-1]


def hand_value(hand: list[str]) -> int:
    total = 0
    aces = 0
    for card in hand:
        rank = card_rank(card)
        if rank == "A":
            aces += 1
            total += 11
        elif rank in {"J", "Q", "K"}:
            total += 10
        else:
            total += int(rank)

    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def is_blackjack(hand: list[str]) -> bool:
    return len(hand) == 2 and hand_value(hand) == 21


def format_hand(hand: list[str], *, hide_first: bool = False) -> str:
    if hide_first and hand:
        return "?? " + " ".join(hand[1:])
    return " ".join(hand)


def asset_url(filename: str) -> str | None:
    return (
        f"attachment://{filename}" if (GAMBLING_ASSET_DIR / filename).exists() else None
    )


def blackjack_image_for_result(result: str | None) -> str:
    if not result:
        return BLACKJACK_TABLE_IMAGE

    result_lower = result.lower()
    if "push" in result_lower or "returned" in result_lower:
        return BLACKJACK_TABLE_IMAGE
    if (
        "you win" in result_lower
        or "dealer busts" in result_lower
        or "you beat" in result_lower
        or ("blackjack" in result_lower and "dealer has" not in result_lower)
    ):
        return BLACKJACK_WIN_IMAGE
    return BLACKJACK_TABLE_IMAGE


class BlackjackView(discord.ui.View):
    def __init__(self, cog, ctx, game_id: int, house_id: int, bet: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = ctx.guild.id
        self.player_id = ctx.author.id
        self.game_id = game_id
        self.house_id = house_id
        self.bet = bet
        self.deck = new_deck()
        self.player_hand = [self.deck.pop(), self.deck.pop()]
        self.dealer_hand = [self.deck.pop(), self.deck.pop()]
        self.finished = False
        self.processing = False
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message(
                "This blackjack table is not yours.", ephemeral=True
            )
            return False
        return True

    def build_embed(
        self,
        house: House | None = None,
        *,
        reveal_dealer: bool = False,
        result: str | None = None,
    ):
        player_total = hand_value(self.player_hand)
        dealer_total = hand_value(self.dealer_hand)
        embed = discord.Embed(
            title="Blackjack",
            color=discord.Color.dark_green() if not result else discord.Color.gold(),
        )
        embed.add_field(
            name="Your Hand",
            value=f"{format_hand(self.player_hand)}\nValue: **{player_total}**",
            inline=False,
        )
        dealer_value = dealer_total if reveal_dealer else "?"
        embed.add_field(
            name="Dealer",
            value=f"{format_hand(self.dealer_hand, hide_first=not reveal_dealer)}\nValue: **{dealer_value}**",
            inline=False,
        )
        embed.add_field(name="Wager", value=f"**{self.bet}** gold", inline=True)
        if house:
            embed.add_field(name="House Treasury", value="**xxxx** gold", inline=True)
        if result:
            embed.description = result
            if url := asset_url(blackjack_image_for_result(result)):
                embed.set_image(url=url)
        else:
            embed.description = "Choose your next move."
            embed.set_footer(
                text="Table times out after 3 minutes. Unfinished hands forfeit the wager."
            )
            if url := asset_url(BLACKJACK_TABLE_IMAGE):
                embed.set_image(url=url)
        if url := asset_url(GOLD_DRAGON_IMAGE):
            embed.set_thumbnail(url=url)
        return embed

    def disable_all(self):
        for child in self.children:
            child.disabled = True

    async def clear_previous_controls(self, interaction: discord.Interaction):
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass

    async def send_table(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        *,
        result: str | None = None,
        view=None,
    ):
        image = blackjack_image_for_result(result)
        kwargs = {
            "embed": embed,
            "files": self.cog._asset_files(image, GOLD_DRAGON_IMAGE),
        }
        if view is not None:
            kwargs["view"] = view
        self.message = await interaction.followup.send(**kwargs)

    async def finish(self, interaction: discord.Interaction, result: str, payout: int):
        self.finished = True
        async with get_session() as session:
            house = await session.get(House, self.house_id)
            if house and payout > 0:
                house.treasury = (house.treasury or 0) + payout
            await session.commit()
            embed = self.build_embed(house, reveal_dealer=True, result=result)
        await self.clear_previous_controls(interaction)
        await self.send_table(interaction, embed, result=result)
        self.cog.active_games.pop((self.guild_id, self.player_id), None)

    async def dealer_play_and_finish(self, interaction: discord.Interaction):
        while hand_value(self.dealer_hand) < 17:
            self.dealer_hand.append(self.deck.pop())
        player_total = hand_value(self.player_hand)
        dealer_total = hand_value(self.dealer_hand)
        if dealer_total > 21:
            await self.finish(
                interaction,
                f"Dealer busts with **{dealer_total}**. You win **{self.bet}** gold.",
                self.bet * 2,
            )
        elif player_total > dealer_total:
            await self.finish(
                interaction,
                f"You beat the dealer, **{player_total}** to **{dealer_total}**. You win **{self.bet}** gold.",
                self.bet * 2,
            )
        elif player_total == dealer_total:
            await self.finish(
                interaction,
                f"Push at **{player_total}**. Your wager is returned.",
                self.bet,
            )
        else:
            await self.finish(
                interaction,
                f"Dealer wins, **{dealer_total}** to **{player_total}**. You lose **{self.bet}** gold.",
                0,
            )

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished or self.processing:
            return
        self.processing = True
        await interaction.response.defer()
        self.player_hand.append(self.deck.pop())
        player_total = hand_value(self.player_hand)
        if player_total > 21:
            return await self.finish(
                interaction,
                f"You bust with **{player_total}**. You lose **{self.bet}** gold.",
                0,
            )
        async with get_session() as session:
            house = await session.get(House, self.house_id)
            embed = self.build_embed(house)
        await self.clear_previous_controls(interaction)
        await self.send_table(interaction, embed, view=self)
        self.processing = False

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished or self.processing:
            return
        self.processing = True
        await interaction.response.defer()
        await self.dealer_play_and_finish(interaction)

    @discord.ui.button(label="Double", style=discord.ButtonStyle.danger)
    async def double(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished or self.processing:
            return
        self.processing = True
        await interaction.response.defer()
        async with get_session() as session:
            house = await session.get(House, self.house_id)
            if not house or (house.treasury or 0) < self.bet:
                await interaction.followup.send(
                    "Insufficient funds to double.", ephemeral=True
                )
                self.processing = False
                return
            house.treasury -= self.bet
            self.bet *= 2
            self.player_hand.append(self.deck.pop())
            await session.commit()
        if hand_value(self.player_hand) > 21:
            return await self.finish(
                interaction, f"Busted with **{hand_value(self.player_hand)}**.", 0
            )
        await self.dealer_play_and_finish(interaction)

    async def on_timeout(self):
        self.cog.active_games.pop((self.guild_id, self.player_id), None)
        self.disable_all()
        if self.message:
            try:
                await self.message.edit(view=self)
            except:
                pass


class GamblingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_games = {}

    def _asset_files(self, *filenames: str) -> list[discord.File]:
        files = []
        seen = set()
        for filename in filenames:
            if filename in seen:
                continue
            seen.add(filename)
            path = GAMBLING_ASSET_DIR / filename
            if path.exists():
                files.append(discord.File(path, filename=filename))
        return files

    async def _player_house(self, session, ctx):
        game = await GameRepo.get_active_game(session, ctx.guild.id)
        if not game:
            return None, None, "No active game."
        stmt = (
            select(GamePlayer)
            .join(User, User.user_id == GamePlayer.user_id)
            .where(User.discord_id == ctx.author.id, GamePlayer.game_id == game.game_id)
            .options(selectinload(GamePlayer.house))
        )
        player = (await session.execute(stmt)).scalars().first()
        if not player or not player.house:
            return game, None, "You do not have an active house claim."
        return game, player.house, None

    def _is_gambling_channel(self, ctx) -> bool:
        return (
            ctx.channel.name in {"gambling-den", "bot-testing", "bot-commands"}
            or ctx.author.guild_permissions.administrator
        )

    @commands.command(name="blackjack", aliases=["bj"])
    async def blackjack(self, ctx, bet: int):
        if not self._is_gambling_channel(ctx):
            return
        if bet <= 0 or bet > 10000:
            return await ctx.send("Bet must be between 1 and 10,000.")
        game_key = (ctx.guild.id, ctx.author.id)
        if game_key in self.active_games:
            return await ctx.send("Finish your current game first.")
        async with get_session() as session:
            game, house, error = await self._player_house(session, ctx)
            if error:
                return await ctx.send(error)
            if (house.treasury or 0) < bet:
                return await ctx.send("Insufficient funds.")
            house.treasury -= bet
            view = BlackjackView(self, ctx, game.game_id, house.house_id, bet)
            self.active_games[game_key] = view
            await session.commit()
            view.message = await ctx.send(
                embed=view.build_embed(house),
                view=view,
                files=self._asset_files(BLACKJACK_TABLE_IMAGE, GOLD_DRAGON_IMAGE),
            )


async def setup(bot):
    await bot.add_cog(GamblingCog(bot))
