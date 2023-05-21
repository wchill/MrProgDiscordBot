from __future__ import annotations

import asyncio
import logging
import platform
import socket
import time
from enum import Enum
from typing import Optional

from discord.ext import commands
from python_logging_rabbitmq import RabbitMQHandlerOneWay


class Emotes:
    OK = "✅"
    ERROR = "❌"


async def run_shell(cmd: str, cwd: Optional[str] = None) -> str:
    proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, cwd=cwd)
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8")


class MessageReaction(Enum):
    OK = "✅"
    ERROR = "❌"
