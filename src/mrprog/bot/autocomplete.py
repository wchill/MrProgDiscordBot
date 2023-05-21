from typing import List, TypeVar

import discord
from discord import app_commands
from mmbn.gamedata.chip import Code
from mrprog.utils.supported_games import GAME_INFO

from . import fuzzy

Choice = TypeVar("Choice")


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

    all_chip_names = sorted(list({chip.name for chip in GAME_INFO[game].all_chips}))
    if not current:
        return limit([app_commands.Choice(name=name, value=name) for name in all_chip_names])

    matches = fuzzy.extract(current, choices=all_chip_names, limit=5, score_cutoff=20)

    ret: list[app_commands.Choice[str]] = []
    for name, _ in matches:
        ret.append(app_commands.Choice(name=name, value=name))

    return limit(ret)


async def chipcode_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)

    chips = GAME_INFO[game].get_chips_by_name(interaction.namespace["chip_name"])
    if len(chips) == 0:
        return limit([app_commands.Choice(name=code.name, value=code.name) for code in Code])

    return [app_commands.Choice(name=chip.code.name, value=chip.code.name) for chip in chips]


async def ncp_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)

    all_part_names = sorted(list({ncp.name for ncp in GAME_INFO[game].all_parts}))
    if not current:
        return limit([app_commands.Choice(name=name, value=name) for name in all_part_names])

    matches = fuzzy.extract(current, choices=all_part_names, limit=5, score_cutoff=20)

    ret: list[app_commands.Choice[str]] = []
    for name, _ in matches:
        ret.append(app_commands.Choice(name=name, value=name))

    return limit(ret)


async def ncpcolor_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)

    color_cls = GAME_INFO[game].get_color_class()
    return [app_commands.Choice(name=color.name, value=color.name) for color in color_cls if color != color_cls.Nothing]
