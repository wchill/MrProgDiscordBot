import discord
from discord import app_commands
from mmbn.gamedata.chip import Code
from mrprog.bot.supported_games import CHIP_LISTS, NCP_LISTS
from mrprog.utils.types import TradeItem


def autocomplete_get_game(interaction: discord.Interaction) -> int:
    if interaction.command.name[-1] in ["3", "4", "5", "6"]:
        return int(interaction.command.name[-1])
    try:
        return interaction.namespace["game"]
    except KeyError:
        return 6


def limit(choices: list[app_commands.Choice[str]]) -> list[app_commands.Choice[str]]:
    if len(choices) <= 25:
        return choices
    return choices[:25]


def _make_choices(items: list[TradeItem], current: str) -> list[app_commands.Choice[str]]:
    items_dict = dict(sorted({item.name.lower(): item for item in items}.items()))
    lower = current.lower()
    return limit(
        [
            app_commands.Choice(name=item.name, value=item.name)
            for name_lower, item in items_dict.items()
            if name_lower.startswith(lower)
        ]
    )


async def chip_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)
    return _make_choices(CHIP_LISTS[game].all_chips, current)


async def chip_autocomplete_restricted(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)
    return _make_choices(CHIP_LISTS[game].tradable_obtainable_chips, current)


async def chipcode_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)

    chips = CHIP_LISTS[game].get_chips_by_name(interaction.namespace["chip_name"])
    codes = [code.name if code != Code.Star else "*" for code in Code]
    if len(chips) == 0:
        return limit([app_commands.Choice(name=code, value=code) for code in codes])

    codes = [chip.code.name if chip.code != Code.Star else "*" for chip in chips]
    return [app_commands.Choice(name=code, value=code) for code in codes]


async def ncp_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)
    return _make_choices(NCP_LISTS[game].all_parts, current)


async def ncp_autocomplete_restricted(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)
    return _make_choices(NCP_LISTS[game].tradable_obtainable_parts, current)


async def ncpcolor_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = autocomplete_get_game(interaction)
    parts = NCP_LISTS[game].get_parts_by_name(interaction.namespace["part_name"])
    choices = [app_commands.Choice(name=part.color.name, value=part.color.name) for part in parts]
    return choices
