import discord
from discord.ui import View, Button, Select, Modal, TextInput
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.db_manager import get_session
from app.db.models import PendingBannerCall


# =========================================================================
# MODAL for Percentage Input (Corrected)
# =========================================================================
class PercentageModal(Modal, title="Adjust Levy Percentage"):
    def __init__(
        self,
        pending_call: PendingBannerCall,
        house_id: int,
        house_name: str,
        current_percent: int,
    ):
        super().__init__()
        self.pending_call_id = pending_call.id
        self.house_id = house_id
        unit_name = "ships" if pending_call.call_type == "SEA" else "troops"

        self.percentage_input = TextInput(
            label=f"New % for {house_name} (0-100)",
            placeholder=f"Current: {current_percent}% of {unit_name}",
            required=True,
            min_length=1,
            max_length=3,
        )
        self.add_item(self.percentage_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_percent = int(self.percentage_input.value)
            if not (0 <= new_percent <= 100):
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "❌ Invalid percentage. Must be a number from 0 to 100.", ephemeral=True
            )

        async with get_session() as session:
            # --- CORE FIX: Eagerly load the relationship to prevent DetachedInstanceError ---
            stmt = (
                select(PendingBannerCall)
                .where(PendingBannerCall.id == self.pending_call_id)
                .options(selectinload(PendingBannerCall.liege_house))
            )
            pending_call = (await session.execute(stmt)).scalars().first()
            if not pending_call:
                return await interaction.response.send_message(
                    "❌ This banner call has expired.", ephemeral=True
                )

            updated_vassal_data = pending_call.vassal_data
            for vassal in updated_vassal_data:
                if vassal["house_id"] == self.house_id:
                    vassal["percent"] = new_percent / 100.0
                    break

            pending_call.vassal_data = updated_vassal_data
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(pending_call, "vassal_data")
            await session.commit()

            # Re-create the view and pass the *already loaded* object to create_embed
            view = BannerControlView(self.pending_call_id)
            await interaction.response.edit_message(
                embed=await view.create_embed(pending_call), view=view
            )


# =========================================================================
# SELECT MENU for Vassal Selection (Corrected)
# =========================================================================
class VassalSelect(Select):
    def __init__(self, pending_call: PendingBannerCall):
        self.pending_call = pending_call
        is_sea = pending_call.call_type == "SEA"
        unit_name, unit_key = (
            ("ships", "max_ships") if is_sea else ("troops", "max_troops")
        )

        options = [
            discord.SelectOption(
                label=v["house_name"],
                value=str(v["house_id"]),
                description=f"{int(v['percent']*100)}% of {v.get(unit_key, 0)} {unit_name}",
            )
            for v in pending_call.vassal_data
            if v.get(unit_key, 0) > 0
        ]

        if not options:
            super().__init__(
                placeholder="No vassals available to adjust.",
                disabled=True,
                options=[discord.SelectOption(label="x", value="x")],
            )
        else:
            super().__init__(
                placeholder="Select a vassal to adjust...", options=options[:25]
            )

    async def callback(self, interaction: discord.Interaction):
        selected_house_id = int(self.values[0])
        vassal = next(
            (
                v
                for v in self.pending_call.vassal_data
                if v["house_id"] == selected_house_id
            ),
            None,
        )
        if vassal:
            modal = PercentageModal(
                self.pending_call,
                selected_house_id,
                vassal["house_name"],
                int(vassal["percent"] * 100),
            )
            await interaction.response.send_modal(modal)


# =========================================================================
# MAIN VIEW for the GM Control Panel (Corrected)
# =========================================================================
class BannerControlView(View):
    def __init__(self, pending_call_id: int):
        super().__init__(timeout=None)
        self.pending_call_id = pending_call_id

    async def create_embed(
        self, pending_call: PendingBannerCall = None
    ) -> discord.Embed:
        """Builds the embed. If a pending_call object isn't provided, it fetches it."""
        if not pending_call:
            async with get_session() as session:
                stmt = (
                    select(PendingBannerCall)
                    .where(PendingBannerCall.id == self.pending_call_id)
                    .options(selectinload(PendingBannerCall.liege_house))
                )
                pending_call = (await session.execute(stmt)).scalars().first()

        if not pending_call:
            return discord.Embed(
                title="Error",
                description="This banner call could not be found.",
                color=discord.Color.red(),
            )

        is_sea = pending_call.call_type == "SEA"
        title, color, unit_name, unit_key = (
            (
                "GM Panel: Naval Levy Call",
                discord.Color.dark_blue(),
                "ships",
                "max_ships",
            )
            if is_sea
            else ("GM Panel: Banner Call", discord.Color.gold(), "troops", "max_troops")
        )

        embed = discord.Embed(title=title, color=color)
        embed.description = (
            f"**{pending_call.liege_house.name}** has called for levies at **{pending_call.rally_point_name}**."
            f"\nReview contributions before mustering."
        )

        status_map = {
            "PENDING_APPROVAL": "🟡 Awaiting command.",
            "COMPLETED": "✅ Mustered.",
            "CANCELLED": "❌ Cancelled.",
        }
        embed.add_field(
            name="Status",
            value=status_map.get(pending_call.status, "Unknown"),
            inline=False,
        )

        vassal_lines, total_units = [], 0
        for v in pending_call.vassal_data:
            if unit_key in v:
                units_to_send = int(v[unit_key] * v["percent"])
                total_units += units_to_send
                vassal_lines.append(
                    f"**{v['house_name']}**: `{int(v['percent']*100)}%` ({units_to_send} / {v[unit_key]} {unit_name})"
                )

        if vassal_lines:
            for i in range(0, len(vassal_lines), 10):
                chunk = vassal_lines[i : i + 10]
                embed.add_field(
                    name=f"Vassal Contributions ({i//10 + 1})",
                    value="\n".join(chunk),
                    inline=False,
                )

        embed.set_footer(text=f"Total Projected Force: {total_units} {unit_name}")

        self.clear_items()
        if pending_call.status == "PENDING_APPROVAL":
            self.add_item(VassalSelect(pending_call))
            self.add_item(
                Button(
                    label="Confirm & Muster",
                    style=discord.ButtonStyle.success,
                    custom_id=f"banner_confirm_{self.pending_call_id}",
                )
            )
            self.add_item(
                Button(
                    label="Cancel Call",
                    style=discord.ButtonStyle.danger,
                    custom_id=f"banner_cancel_{self.pending_call_id}",
                )
            )

        self.add_item(
            Button(
                label="How to Use",
                style=discord.ButtonStyle.blurple,
                custom_id=f"banner_help_{self.pending_call_id}",
            )
        )

        return embed
