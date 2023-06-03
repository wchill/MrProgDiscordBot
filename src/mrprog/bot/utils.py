from __future__ import annotations

import asyncio
from enum import Enum
from typing import Optional

from discord import Interaction, app_commands


def owner_only():
    # the check
    async def actual_check(interaction: Interaction):
        return await interaction.client.is_owner(interaction.user)

    # returning the check
    return app_commands.check(actual_check)


class Emotes:
    OK = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    STEAM = "<:Steam:1102729564230254643>"
    SWITCH = "<:Switch:1102729586040647710>"


class MessageReaction(Enum):
    OK = "✅"
    ERROR = "❌"
