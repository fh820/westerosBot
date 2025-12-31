# app/ui/banner_view.py

import discord
from discord.ui import View, Button, Modal, TextInput
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.db.db_manager import get_session
from app.db.models import PendingBannerCall, House


# =========================================================================
# MODAL: Input House Name & Percentage
# =========================================================================
class AdjustVassalModal(Modal, title="Adjust Levy Contribution"):
    def __init__(self, pending_call_id: int, call_type: str):
        super().__init__()
        self.pending_call_id = pending_call_id

        unit_name = "ships" if call_type == "SEA" else "troops"

        self.house_name_input = TextInput(
            label="House Name",
            placeholder="e.g. Hightower",
            required=True,
            min_length=3,
        )

        self.percent_input = TextInput(
            label="New Percentage (0-100)",
            placeholder=f"e.g. 50 (sends 50% of {unit_name})",
            required=True,
            min_length=1,
            max_length=3,
        )

        self.add_item(self.house_name_input)
        self.add_item(self.percent_input)

    async def on_submit(self, interaction: discord.Interaction):
        # 1. Parse Inputs
        target_name = self.house_name_input.value.strip()
        try:
            new_percent = int(self.percent_input.value)
            if not (0 <= new_percent <= 100):
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "❌ Percentage must be a number between 0 and 100.", ephemeral=True
            )

        async with get_session() as session:
            # Load Pending Call
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

            # 2. Find the House ID based on the text input (Fuzzy Match)
            # We search inside the JSON data first to ensure they are actually in this muster
            target_house_data = None
            for v in pending_call.vassal_data:
                # Case-insensitive check
                if v["house_name"].lower() == target_name.lower():
                    target_house_data = v
                    break

            # Smart check: try "House X" if "X" failed
            if not target_house_data and not target_name.lower().startswith("house"):
                for v in pending_call.vassal_data:
                    if v["house_name"].lower() == f"house {target_name}".lower():
                        target_house_data = v
                        break

            if not target_house_data:
                return await interaction.response.send_message(
                    f"❌ Could not find **{target_name}** in the list of vassals for this call.\n"
                    f"Check the spelling or ensure they are an NPC vassal.",
                    ephemeral=True,
                )

            target_house_id = target_house_data["house_id"]
            real_house_name = target_house_data["house_name"]

            # =========================================================
            # CASCADING UPDATE LOGIC (Recursive)
            # =========================================================
            new_decimal = new_percent / 100.0
            updated_vassal_data = list(pending_call.vassal_data)

            # 1. Get all House IDs involved in this specific banner call
            all_involved_ids = [v["house_id"] for v in updated_vassal_data]

            # 2. Fetch DB objects for ALL these houses to map out relationships
            # We need to know who is the liege of whom within this specific group
            stmt_hierarchy = select(House.house_id, House.liege_id).where(
                House.house_id.in_(all_involved_ids)
            )
            hierarchy_rows = (await session.execute(stmt_hierarchy)).all()

            # Build an adjacency list: parent_id -> [child_id, child_id...]
            vassal_map = {}
            for h_id, l_id in hierarchy_rows:
                if l_id not in vassal_map:
                    vassal_map[l_id] = []
                vassal_map[l_id].append(h_id)

            # 3. Recursive Helper to find all descendants
            def get_all_descendants(parent_id):
                descendants = set()
                direct_children = vassal_map.get(parent_id, [])
                for child in direct_children:
                    # Only add if child is actually in our banner call list
                    if child in all_involved_ids:
                        descendants.add(child)
                        descendants.update(get_all_descendants(child))
                return descendants

            # Get the set of IDs to update (Target + All Descendants)
            ids_to_update = get_all_descendants(target_house_id)
            ids_to_update.add(target_house_id)

            # 4. Apply the updates
            count = 0
            for vassal in updated_vassal_data:
                if vassal["house_id"] in ids_to_update:
                    vassal["percent"] = new_decimal
                    count += 1

            # Save
            pending_call.vassal_data = updated_vassal_data
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(pending_call, "vassal_data")
            await session.commit()

            # Refresh View
            view = BannerControlView(self.pending_call_id)
            await interaction.response.edit_message(
                embed=await view.create_embed(pending_call), view=view
            )

            msg = f"✅ Set **{real_house_name}** to **{new_percent}%**."
            if count > 1:
                msg += f" (Also updated {count-1} of their sub-vassals)."

            await interaction.followup.send(msg, ephemeral=True)


# =========================================================================
# MAIN VIEW
# =========================================================================
class BannerControlView(View):
    def __init__(self, pending_call_id: int):
        super().__init__(timeout=None)
        self.pending_call_id = pending_call_id

    async def create_embed(
        self, pending_call: PendingBannerCall = None
    ) -> discord.Embed:
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
                title="Error", description="Call not found.", color=discord.Color.red()
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
            f"**{pending_call.liege_house.name}** has called for levies at **{pending_call.rally_point_name}**.\n"
            f"Click **Adjust Contribution** to change percentages."
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

        # Build list
        vassal_lines = []
        total_units = 0

        # Sort alphabetically for easier reading
        sorted_vassals = sorted(pending_call.vassal_data, key=lambda x: x["house_name"])

        for v in sorted_vassals:
            if unit_key in v:
                units_to_send = int(v[unit_key] * v["percent"])
                total_units += units_to_send

                # Check for sub-vassal tag (added by heavy_tasks) for display
                # v.get('tag') might contain " [Hightower]"
                display_name = f"**{v['house_name']}**{v.get('tag', '')}"

                vassal_lines.append(
                    f"{display_name}: `{int(v['percent']*100)}%` ({units_to_send} / {v[unit_key]})"
                )

        # Pagination for Embed Fields (Discord limit 1024 chars per field)
        if vassal_lines:
            # Join lines and split into chunks of ~15 lines
            chunk_size = 15
            for i in range(0, len(vassal_lines), chunk_size):
                chunk = vassal_lines[i : i + chunk_size]
                embed.add_field(
                    name=f"Vassal Contributions ({i//chunk_size + 1})",
                    value="\n".join(chunk),
                    inline=False,
                )

        embed.set_footer(text=f"Total Projected Force: {total_units} {unit_name}")

        self.clear_items()

        if pending_call.status == "PENDING_APPROVAL":
            # --- NEW BUTTON REPLACING SELECT MENU ---
            self.add_item(
                Button(
                    label="Adjust Contribution",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"banner_adjust_{self.pending_call_id}",
                    emoji="✏️",
                )
            )

            self.add_item(
                Button(
                    label="Confirm & Muster",
                    style=discord.ButtonStyle.success,
                    custom_id=f"banner_confirm_{self.pending_call_id}",
                    row=1,
                )
            )
            self.add_item(
                Button(
                    label="Cancel Call",
                    style=discord.ButtonStyle.danger,
                    custom_id=f"banner_cancel_{self.pending_call_id}",
                    row=1,
                )
            )

        self.add_item(
            Button(
                label="Help",
                style=discord.ButtonStyle.secondary,
                custom_id=f"banner_help_{self.pending_call_id}",
                row=2,
            )
        )

        return embed
