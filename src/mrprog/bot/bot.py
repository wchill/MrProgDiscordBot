from __future__ import annotations

import argparse
import asyncio

import discord
from discord.ext import commands
from mrprog.utils.logging import install_logger

COGS = ["info", "admin", "trade"]


class MrProgBot(discord.ext.commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        self.guild = discord.Object(id=741898427000029256)

        super().__init__(command_prefix="!", owner_id=174603401479323649, intents=intents)

    async def on_ready(self):
        print(f"Connected to {len(self.guilds)} servers")


bot = MrProgBot()


async def main():
    parser = argparse.ArgumentParser(prog="Mr. Prog Discord Bot", description="Bot process for Mr. Prog")
    parser.add_argument("--host")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--token")
    parser.parse_args()

    install_logger(parser.host, parser.username, parser.password)
    bot.config = {"host": parser.host, "username": parser.username, "password": parser.password}

    await bot.login(parser.token)
    for ext in COGS:
        await bot.load_extension(f"mrprog.bot.cogs.{ext}")
    await bot.connect()


if __name__ == "__main__":
    asyncio.run(main())
