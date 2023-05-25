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
    STEAM = "<:steam:1087038464769396836>"
    SWITCH = "<:Switch:1102729586040647710>"


async def run_shell(cmd: str, cwd: Optional[str] = None) -> str:
    proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, cwd=cwd)
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8")


class MessageReaction(Enum):
    OK = "✅"
    ERROR = "❌"
