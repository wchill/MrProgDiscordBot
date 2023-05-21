import asyncio
import datetime
import json
import math
import os
import platform
import sys
from typing import Literal, Optional

import cpuinfo
import discord
import psutil
from discord import app_commands
from discord.ext import commands

from .. import utils
from ..utils import MessageReaction

RESTART_FILE = os.path.expanduser("~/.bot_restarted")


class AdminCog(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @commands.Cog.listener()
    async def on_ready(self):
        if os.path.exists(RESTART_FILE):
            with open(RESTART_FILE, "r") as f:
                message_info = json.load(f)
            if message_info.get("token") is not None:
                app_id = message_info["application_id"]
                token = message_info["token"]
                webhook = discord.Webhook.partial(id=app_id, token=token, client=self.bot)
                await webhook.send(content="Successfully restarted!")
            else:
                discord_channel = self.bot.get_channel(message_info["channel_id"])
                discord_message = await discord_channel.fetch_message(message_info["message_id"])
                await discord_message.reply("Successfully restarted!")
            os.unlink(RESTART_FILE)
        print("Admin cog successfully loaded")

    @commands.command()
    @commands.guild_only()
    @commands.is_owner()
    async def sync(
        self,
        ctx: commands.Context,
        guilds: commands.Greedy[discord.Object],
        spec: Optional[Literal["~", "*", "^"]] = None,
    ) -> None:
        if not guilds:
            if spec == "~":
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "*":
                ctx.bot.tree.copy_global_to(guild=ctx.guild)
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "^":
                ctx.bot.tree.clear_commands(guild=ctx.guild)
                await ctx.bot.tree.sync(guild=ctx.guild)
                synced = []
            else:
                synced = await ctx.bot.tree.sync()

            await ctx.send(f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}")
            return

        ret = 0
        for guild in guilds:
            try:
                await ctx.bot.tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")

    reload_parent_command = app_commands.Group(name="reload", description="...")

    @reload_parent_command.command(name="cog")
    @commands.is_owner()
    async def reload_cog(self, interaction: discord.Interaction, cog_name: str) -> None:
        await interaction.response.defer()
        try:
            await self.bot.reload_extension(cog_name)
            await interaction.followup.send(content=f"{cog_name} reloaded")
        except Exception as e:
            await interaction.followup.send(content=f"Error when reloading {cog_name}: {e}")

    @reload_cog.autocomplete("cog_name")
    async def cog_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=name, value=name) for name in self.bot.extensions.keys()]

    @reload_parent_command.command(name="allcogs")
    @commands.is_owner()
    async def reload_all_cogs(self, interaction: discord.Interaction):
        cogs = list(self.bot.cogs.keys())
        await interaction.defer()
        for cog in cogs:
            try:
                await self.bot.reload_extension(cog)
            except Exception as e:
                await interaction.followup.send(content=f"Error when reloading {cog}: {e}")
                return
        cogs_list = '", "'.join(cogs)
        await interaction.followup.send(content=f'Successfully reloaded {len(cogs)} cogs: "{cogs_list}"')

    @reload_parent_command.command(name="bot")
    @commands.is_owner()
    async def reload_bot(self, ctx: commands.Context):
        await ctx.message.add_reaction(MessageReaction.OK.value)
        await ctx.reply("Restarting the bot now!")
        self.save_restart_context(ctx)

        os.system("systemctl restart discord-bot")

    @reload_parent_command.command(name="system")
    @commands.is_owner()
    async def reload_system(self, ctx: commands.Context):
        await ctx.message.add_reaction(MessageReaction.OK.value)
        await ctx.reply("Restarting the bot now!")
        self.save_restart_context(ctx)

        os.system("reboot")

    @reload_parent_command.command(name="code")
    @commands.is_owner()
    async def update(self, ctx: commands.Context):
        await ctx.message.add_reaction(MessageReaction.OK.value)
        await ctx.reply("Updating the bot now!")
        self.save_restart_context(ctx)
        await utils.run_shell("git fetch --all && git reset --hard origin/master", cwd=os.path.dirname(__file__))

        os.system("systemctl restart discord-bot")

    @staticmethod
    def save_restart_context(ctx: commands.Context) -> None:
        context = {
            "channel_id": ctx.channel.id if ctx.channel is not None else None,
            "message_id": ctx.message.id if ctx.channel is not None else None,
            "application_id": ctx.interaction.application_id if ctx.interaction is not None else None,
            "token": ctx.interaction.token if ctx.interaction is not None else None,
        }

        with open(RESTART_FILE, "w") as f:
            json.dump(context, f)

    @staticmethod
    async def run_cpuinfo():
        def _utf_to_str(utf):
            if isinstance(utf, list):
                return [_utf_to_str(element) for element in utf]
            elif isinstance(utf, dict):
                return {_utf_to_str(key): _utf_to_str(value) for key, value in utf.items()}
            else:
                return utf

        async def get_cpu_info_json():
            p1 = await asyncio.create_subprocess_exec(
                sys.executable, cpuinfo.cpuinfo.__file__, "--json", stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await p1.communicate()

            if p1.returncode != 0:
                return "{}"

            return stdout.decode(encoding="UTF-8")

        data = await get_cpu_info_json()
        return json.loads(data, object_hook=_utf_to_str)

    @app_commands.command()
    @commands.is_owner()
    async def botstatus(self, interaction: discord.Interaction):
        try:
            load1, load5, load15 = psutil.getloadavg()
            virt_mem = psutil.virtual_memory()
            disk_usage = psutil.disk_usage(os.path.abspath("."))

            creation_time = psutil.Process(os.getpid()).create_time()
            boot_time = psutil.boot_time()
            unix_timestamp = (datetime.datetime.utcnow() - datetime.datetime(1970, 1, 1)).total_seconds()
            uptime = math.floor(unix_timestamp - creation_time)
            system_uptime = math.floor(unix_timestamp - boot_time)

            cpu_info = await self.run_cpuinfo()
            soc = cpu_info.get("hardware_raw")
            cpu_name = cpu_info.get("brand_raw")
            display_cpu_name = f"{soc} ({cpu_name})" if soc else cpu_name
            architecture = platform.uname().machine
            python_ver = cpu_info.get("python_version")
            clock_speed = cpu_info.get("hz_actual_friendly")

            core_count = psutil.cpu_count(False)
            thread_count = psutil.cpu_count()

            if platform.system() == "Windows":
                output = await utils.run_shell(
                    'powershell.exe -c "Get-CimInstance Win32_OperatingSystem | Select Caption, Version | ConvertTo-Json"'
                )
                data = json.loads(output)
                os_name = data["Caption"]
                os_build = data["Version"]
            else:
                uname = platform.uname()
                os_name = f"{uname.system} {uname.release}"
                os_build = uname.version

            git_version = (await utils.run_shell("git describe --always", cwd=os.path.dirname(__file__))).strip()

            embed = discord.Embed(title="Bot status")
            embed.add_field(name="Hostname", value=platform.node())
            embed.add_field(name="OS", value=f"{os_name}")
            embed.add_field(name="OS build", value=os_build)
            embed.add_field(
                name="CPU",
                value=f"{display_cpu_name} ({architecture}, {clock_speed}, {core_count} cores, {thread_count} threads)",
            )
            embed.add_field(name="CPU usage", value=f"Load average (1/5/15 min):\n{load1}, {load5}, {load15}")
            embed.add_field(
                name="Memory usage", value=f"{virt_mem[3]/(1024 ** 2):.2f}/{virt_mem[0]/(1024 ** 2):.2f} MB"
            )
            embed.add_field(
                name="Disk usage", value=f"{disk_usage.used/(1024 ** 3):.2f}/{disk_usage.total/(1024 ** 3):.2f} GB"
            )
            embed.add_field(name="Python version", value=python_ver)
            embed.add_field(name="Discord.py version", value=discord.__version__)
            embed.add_field(name="Bot version (git)", value=git_version)
            embed.add_field(name="Bot uptime", value=str(datetime.timedelta(seconds=uptime)))
            embed.add_field(name="System uptime", value=str(datetime.timedelta(seconds=system_uptime)))

            await interaction.response.send_message(embed=embed)
        except Exception:
            import traceback

            traceback.print_exc()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
