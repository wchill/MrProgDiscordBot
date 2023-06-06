from __future__ import annotations

import argparse
import asyncio
import logging
import signal

import discord
from discord.ext import commands
from mrprog.utils.logging import install_logger

logger = logging.getLogger(__name__)
COGS = ["info", "admin", "trade", "save"]


class MrProgBot(discord.ext.commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(command_prefix="!", owner_id=174603401479323649, intents=intents)

    async def on_ready(self):
        logger.info(f"Connected to {len(self.guilds)} servers")


bot = MrProgBot()


def signal_handler(n, frame):
    fut = asyncio.run_coroutine_threadsafe(bot.close(), bot.loop)
    fut.result()


async def main():
    parser = argparse.ArgumentParser(prog="Mr. Prog Discord Bot", description="Bot process for Mr. Prog")
    parser.add_argument("--host")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--token")
    args = parser.parse_args()

    install_logger(args.host, args.username, args.password)
    bot.config = {"host": args.host, "username": args.username, "password": args.password}

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Logging in")
    await bot.login(args.token)
    for ext in COGS:
        logger.info(f"Loading {ext}")
        await bot.load_extension(f"mrprog.bot.cogs.{ext}")
    logger.info(f"Connecting")
    await bot.connect()


if __name__ == "__main__":
    asyncio.run(main())
