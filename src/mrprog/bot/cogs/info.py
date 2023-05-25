import io
import logging
import time

import discord
from discord import Colour, app_commands
from discord.ext import commands
from mmbn.gamedata.chip import Chip, Code
from mmbn.gamedata.navicust_part import COLORS, ColorLiteral
from mrprog.utils.supported_games import GAME_INFO, SupportedGameLiteral

from mrprog.bot.utils import Emotes

from .. import autocomplete

logger = logging.getLogger(__name__)


class InfoCog(commands.Cog, name="Info"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_beg = time.time() - 600
        super().__init__()

    @commands.Cog.listener()
    async def on_ready(self):
        logger.debug("Info cog successfully loaded")

    @app_commands.command(description="Lists all the chips that come in a particular code in the chosen game.")
    async def chipcode(self, interaction: discord.Interaction, game: SupportedGameLiteral, chip_code: str):
        try:
            if chip_code == "*":
                actual_chip_code = Code.Star
            else:
                actual_chip_code = Code[chip_code.upper()]
        except KeyError:
            await interaction.response.send_message(f"{Emotes.ERROR} That code isn't valid.", ephemeral=True)
            return

        matching_chips = []
        for chip in GAME_INFO[game].all_chips:
            if chip.code == actual_chip_code:
                matching_chips.append(chip)

        embed = discord.Embed(
            title=f"BN{game} chips in {chip_code.upper()} code",
            color=Colour.gold(),
            description=", ".join([chip.name for chip in matching_chips]),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Describes a chip in the chosen game.")
    @app_commands.autocomplete(chip_name=autocomplete.chip_autocomplete)
    async def chip(self, interaction: discord.Interaction, game: SupportedGameLiteral, chip_name: str):
        chips = GAME_INFO[game].get_chips_by_name(chip_name)
        if len(chips) == 0:
            await interaction.response.send_message(f"{Emotes.ERROR} That chip doesn't exist.", ephemeral=True)
            return

        chip = chips[0]
        embed = discord.Embed(title=f"{chip.name} (BN{game})")

        embed.add_field(name="Description", value=chip.description, inline=False)
        embed.add_field(name="ID", value=chip.chip_id)
        embed.add_field(name="Codes", value=", ".join(c.code.name if c.code != Code.Star else "*" for c in chips))
        embed.add_field(
            name="Type", value={Chip.STANDARD: "Standard", Chip.MEGA: "Mega", Chip.GIGA: "Giga"}[chip.chip_type]
        )
        embed.add_field(name="Attack", value=chip.atk if chip.atk > 1 else "???" if chip.atk == 1 else "---")
        embed.add_field(name="Element", value=chip.element.name)
        embed.add_field(name="MB", value=f"{chip.mb} MB")

        try:
            image = discord.File(chip.chip_image_path, filename="chip.png")
            embed.set_image(url="attachment://chip.png")
            await interaction.response.send_message(embed=embed, file=image)
        except NotImplementedError:
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="ncp", description="Describes a NaviCust part in the chosen game.")
    @app_commands.autocomplete(part_name=autocomplete.ncp_autocomplete)
    async def ncp(self, interaction: discord.Interaction, game: SupportedGameLiteral, part_name: str):
        parts = GAME_INFO[game].get_parts_by_name(part_name)
        if len(parts) == 0:
            await interaction.response.send_message(f"{Emotes.ERROR} That part doesn't exist.", ephemeral=True)
            return

        part = parts[0]
        with io.BytesIO() as img:
            img.write(part.block_image)
            img.seek(0)
            image = discord.File(img, filename="ncp.png")
        embed = discord.Embed(title=f"{part.name} (BN{game})")
        embed.set_image(url="attachment://ncp.png")
        embed.add_field(name="Description", value=part.description, inline=False)
        embed.add_field(name="Colors", value=" ".join(p.color.value for p in parts))
        embed.add_field(name="Compression code", value=part.compression_code if part.compression_code else "None")
        embed.add_field(name="Bug", value=part.bug.value, inline=False)

        await interaction.response.send_message(embed=embed, file=image)

    @app_commands.command(description="Lists all NaviCust parts of the same color in the chosen game.")
    async def ncpcolor(self, interaction: discord.Interaction, game: SupportedGameLiteral, color: ColorLiteral):
        try:
            actual_color = GAME_INFO[game].get_color(color)
        except ValueError:
            await interaction.response.send_message(
                f'{Emotes.ERROR} "{color}" is not a valid color in BN{game}.', ephemeral=True
            )
            return

        text = []
        for part in GAME_INFO[game].all_parts:
            if part.color == actual_color:
                text.append(part.name)

        embed = discord.Embed(
            title=f"{actual_color.value} BN{game} {actual_color.name} NaviCust parts",
            color=Colour.from_rgb(*COLORS[actual_color.name]),
            description="\n".join(text),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InfoCog(bot))
