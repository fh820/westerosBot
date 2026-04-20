import discord
from discord.ext import commands

from app.db.db_manager import get_session
from app.db.repositories import GameRepo
from app.services.scouting_service import ScoutingService


class ScoutingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _format_report_embed(self, report):
        result = report.result or {}
        title = f"Scout Report #{report.id} - {report.confidence.title()}"
        embed = discord.Embed(title=title, color=discord.Color.dark_green())

        if report.report_type == "area":
            embed.add_field(
                name="Area",
                value=f"{result.get('location', 'Unknown')} ({result.get('region', 'Unknown')})",
                inline=False,
            )
            forces = result.get("forces", [])
            if forces:
                lines = []
                for force in forces[:8]:
                    lines.append(
                        f"**{force.get('target_name', 'Unknown')}** of {force.get('target_house', 'Unknown')}: "
                        f"{force.get('estimated_size', 'unknown')} ({force.get('estimated_count', 'unclear')}), "
                        f"{force.get('status', 'unclear')} - {force.get('confidence', 'poor')}"
                    )
                embed.add_field(name="Detected Forces", value="\n".join(lines), inline=False)
            else:
                embed.add_field(name="Detected Forces", value="No clear forces found.", inline=False)
        else:
            embed.add_field(
                name="Target",
                value=f"{result.get('target_name', 'Unknown')} of {result.get('target_house', 'Unknown')}",
                inline=False,
            )
            embed.add_field(
                name="Strength",
                value=f"{result.get('estimated_size', 'unknown')} ({result.get('estimated_count', 'unclear')})",
                inline=True,
            )
            embed.add_field(name="Status", value=result.get("status", "unclear"), inline=True)
            embed.add_field(name="Terrain", value=result.get("terrain", "unknown"), inline=True)

            comp = result.get("composition", {})
            if isinstance(comp, dict):
                comp_text = "\n".join(f"{k.title()}: {v}" for k, v in comp.items())
            else:
                comp_text = str(comp)
            embed.add_field(name="Composition", value=comp_text or "unclear", inline=False)
            embed.add_field(name="Morale", value=result.get("morale_hint", "unknown"), inline=True)
            embed.add_field(name="Supply", value=result.get("supply_hint", "unknown"), inline=True)
            embed.add_field(name="Likely Plan", value=result.get("likely_plan", "unknown"), inline=True)

        warnings = result.get("warnings", [])
        if warnings:
            embed.add_field(name="Warnings", value="\n".join(warnings), inline=False)
        embed.set_footer(text="Intel is fuzzy and expires after roughly 7 days.")
        return embed

    async def _send_target_alert(self, alert):
        if not alert:
            return
        channel = self.bot.get_channel(alert.get("channel_id"))
        if not channel:
            return
        mention = f"<@{alert['discord_id']}>" if alert.get("discord_id") else None
        try:
            await channel.send(content=mention, embed=discord.Embed(
                title="Scouts Sighted",
                description=alert.get("message", "Enemy scouts were seen near your lines."),
                color=discord.Color.orange(),
            ))
        except Exception:
            pass

    @commands.command(name="scout")
    async def scout_cmd(self, ctx, scout_army_id: int, target_army_id: int):
        """Scout a known army or fleet."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("No active game.")

            service = ScoutingService(session)
            success, msg, report, alert = await service.scout_army(
                game.game_id,
                ctx.author.id,
                scout_army_id,
                target_army_id,
                is_gm=ctx.author.guild_permissions.administrator,
            )
            if not success:
                return await ctx.send(f"ERROR {msg}")

            await ctx.send(embed=self._format_report_embed(report))
            await self._send_target_alert(alert)

    @commands.command(name="scout_area", aliases=["scout-area"])
    async def scout_area_cmd(self, ctx, scout_army_id: int, *, location_name: str):
        """Scout around a named fief/location."""
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("No active game.")

            service = ScoutingService(session)
            success, msg, report, alert = await service.scout_area(
                game.game_id,
                ctx.author.id,
                scout_army_id,
                location_name,
                is_gm=ctx.author.guild_permissions.administrator,
            )
            if not success:
                return await ctx.send(f"ERROR {msg}")

            await ctx.send(embed=self._format_report_embed(report))
            await self._send_target_alert(alert)

    @commands.command(name="intel")
    async def intel_cmd(self, ctx, limit: int = 5):
        """Shows recent scout reports for your house."""
        limit = max(1, min(limit, 10))
        async with get_session() as session:
            game = await GameRepo.get_active_game(session, ctx.guild.id)
            if not game:
                return await ctx.send("No active game.")

            service = ScoutingService(session)
            reports = await service.recent_reports(
                game.game_id,
                ctx.author.id,
                limit=limit,
                is_gm=ctx.author.guild_permissions.administrator,
            )
            if not reports:
                return await ctx.send("No scout reports found.")

            for report in reports:
                await ctx.send(embed=self._format_report_embed(report))


async def setup(bot):
    await bot.add_cog(ScoutingCog(bot))
