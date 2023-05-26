from typing import List, TypeVar

import discord
from discord import app_commands
from discord.app_commands import Choice
from mmbn.gamedata.chip import Code
from mrprog.utils.supported_games import GAME_INFO


def autocomplete_get_game(interaction: discord.Interaction) -> int:
    if interaction.command.name[-1] in ["3", "6"]:
        return int(interaction.command.name[-1])
    try:
        return interaction.namespace["game"]
    except KeyError:
        return 6


def limit(choices: List[Choice]) -> List[Choice]:
    if len(choices) <= 25:
        return choices
    return choices[:25]


async def chip_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)

    all_chips = dict(sorted({chip.name.lower(): chip for chip in GAME_INFO[game].all_chips}.items()))
    return limit(
        [
            app_commands.Choice(name=chip.name, value=chip.name)
            for name_lower, chip in all_chips.items()
            if name_lower.startswith(current.lower())
        ]
    )


async def chipcode_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)

    chips = GAME_INFO[game].get_chips_by_name(interaction.namespace["chip_name"])
    codes = [code.name if code != Code.Star else "*" for code in Code]
    if len(chips) == 0:
        return limit([app_commands.Choice(name=code, value=code) for code in codes])

    codes = [chip.code.name if chip.code != Code.Star else "*" for chip in chips]
    return [app_commands.Choice(name=code, value=code) for code in codes]


async def ncp_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)

    all_parts = dict(sorted({ncp.name.lower(): ncp for ncp in GAME_INFO[game].all_parts}.items()))
    return limit(
        [
            app_commands.Choice(name=ncp.name, value=ncp.name)
            for name_lower, ncp in all_parts.items()
            if name_lower.startswith(current.lower())
        ]
    )


async def ncpcolor_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)
    parts = GAME_INFO[game].get_parts_by_name(interaction.namespace["part_name"])
    choices = [app_commands.Choice(name=part.color.name, value=part.color.name) for part in parts]
    return choices
