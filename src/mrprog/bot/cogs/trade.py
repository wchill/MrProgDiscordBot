import asyncio
import collections
import io
import json
import logging
import re
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.app_commands import AppCommandError
from discord.ext import commands, tasks
from mmbn.gamedata.chip import Code
from mrprog.utils.supported_games import (
    GAME_INFO,
    SupportedGameLiteral,
    SupportedPlatformLiteral,
)
from mrprog.utils.trade import TradeRequest
from mrprog.utils.types import TradeItem

from mrprog.bot import autocomplete
from mrprog.bot.rpc_client import TradeRequestRpcClient, TradeResponse
from mrprog.bot.stats.trade_stats import BotTradeStats
from mrprog.bot.utils import Emotes, owner_only

logger = logging.getLogger(__name__)


class RequestGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="request", description="...", guild_only=True)


class TradeCog(commands.Cog, name="Trade"):
    request_group = RequestGroup()

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.trade_request_rpc_client = None
        self.bot_stats = None

        self.channel_ids = set()
        self.queue_message = None
        self.queue_message_channel = None

    @tasks.loop(seconds=60)
    async def change_status(self):
        if self.bot.is_ready():
            await self.bot.change_presence(
                status=discord.Status.online,
                activity=discord.Game(
                    name=f"{self.bot_stats.get_total_trade_count()} trades to "
                    f"{self.bot_stats.get_total_user_count()} users",
                ),
            )

    @tasks.loop(seconds=10)
    async def update_queue(self):
        if self.bot.is_ready():
            embed = self._make_queue_embed()
            await self.queue_message.edit(content="", embed=embed)

    async def cog_load(self) -> None:
        self.bot_stats = BotTradeStats.load_or_default("bot_stats.pkl")
        self.change_status.start()
        self.update_queue.start()

        self.trade_request_rpc_client = TradeRequestRpcClient(
            "bn-orchestrator", "worker", "worker", self.message_room_code, self.handle_trade_update
        )

        await self.trade_request_rpc_client.connect()
        config_bytestring = await self.trade_request_rpc_client.wait_for_message("bot/config")
        config = json.loads(config_bytestring.decode("utf-8"))

        channel = await self.bot.fetch_channel(int(config["queue_message_channel"]))
        embed = self._make_queue_embed()
        if config.get("queue_message_id") is not None:
            try:
                queue_message_id = int(config["queue_message_id"])
                self.queue_message = await channel.fetch_message(queue_message_id)
                await self.queue_message.edit(content="", embed=embed)
                logger.debug("Trade cog successfully loaded")
                return
            except Exception:
                pass

        self.queue_message = await channel.send(embed=embed)
        config["queue_message_id"] = str(self.queue_message.id)

        await self.trade_request_rpc_client.publish_retained_message("bot/config", json.dumps(config))
        logger.debug("Trade cog successfully loaded")

    async def cog_unload(self) -> None:
        await self.trade_request_rpc_client.disconnect()
        self.bot_stats.save("bot_stats.pkl")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: AppCommandError):
        import traceback

        logger.exception(traceback.format_exception(error))

    def _make_queue_embed(self, requested_user: Optional[discord.User] = None) -> discord.Embed:
        queued, in_progress, _ = self.trade_request_rpc_client.get_current_queue()

        fields = collections.defaultdict(list)
        for correlation_id, request in queued:
            lines = fields[(request.system, request.game)]
            count = len(lines)

            if count >= 20:
                continue

            lines.append(f"{count}. <@{request.user_id}> - `{request.trade_item}`")

        embed = discord.Embed(title=f"Current queue ({len(queued)})")

        for (system, game), lines in sorted(fields.items(), key=lambda kv: kv[0]):
            system_emote = Emotes.STEAM if system == "steam" else Emotes.SWITCH
            embed.add_field(name=f"{system_emote} BN{game} ({len(lines)})", value="\n".join(lines))

        lines = []
        count = 0
        for user_id, response in in_progress:
            if count >= 30:
                break
            count += 1
            request = response.request
            system_emote = Emotes.STEAM if request.system == "steam" else Emotes.SWITCH
            lines.append(
                f"{count}. <@{request.user_id}> - `{request.trade_item}` ({system_emote} BN{request.game}, worker {response.worker_id[:8]})"
            )
        embed.add_field(name="In progress", value="\n".join(lines) if lines else "No one", inline=False)

        if requested_user is not None:
            for idx, (user_id, response) in enumerate(queued):
                if requested_user.id == user_id:
                    embed.set_footer(
                        text=f"Your position in the queue is {idx + 1}", icon_url=requested_user.display_avatar.url
                    )
                    break
            else:
                for idx, (user_id, response) in enumerate(in_progress):
                    if requested_user.id == user_id:
                        embed.set_footer(text="Your trade is in progress", icon_url=requested_user.display_avatar.url)
                        break
        else:
            embed.set_footer(text="This message updates every 10 seconds")

        return embed

    async def handle_trade_update(self, trade_response: TradeResponse):
        try:
            await self.bot.wait_until_ready()
            discord_channel = self.bot.get_channel(trade_response.request.channel_id)

            if trade_response.message or trade_response.embed or trade_response.image:
                emote = Emotes.OK if trade_response.status == TradeResponse.SUCCESS else Emotes.ERROR
                img = None
                content = None
                embed = None

                if trade_response.image:
                    img = discord.File(fp=io.BytesIO(trade_response.image), filename="image.png")

                if trade_response.message:
                    if trade_response.status in [TradeResponse.FAILURE, TradeResponse.CRITICAL_FAILURE]:
                        content = f"{emote} <@{trade_response.request.user_id}>: {trade_response.message}\n\n<@{self.bot.owner_id}>"
                    else:
                        content = f"{emote} <@{trade_response.request.user_id}>: {trade_response.message}"

                if trade_response.embed:
                    embed = discord.Embed.from_dict(trade_response.embed)

                await discord_channel.send(content=content, embed=embed, file=img)

            if trade_response.status == TradeResponse.SUCCESS:
                self.bot_stats.add_trade(trade_response.request.user_id, trade_response.request.trade_item)
                self.bot_stats.save("bot_stats.pkl")
        except Exception:
            import traceback

            traceback.print_exc()

    async def message_room_code(self, trade_response: TradeResponse):
        user = await self.bot.fetch_user(trade_response.request.user_id)
        await user.send(
            f"Your `{trade_response.request.trade_item}` is ready! You have 3 minutes to join",
            silent=False,
            file=discord.File(fp=io.BytesIO(trade_response.image), filename="roomcode.png"),
        )

    requestfor_group = app_commands.Group(name="requestfor", description="...")

    @request_group.command(name="chip", description="Request a chip")
    @app_commands.autocomplete(chip_name=autocomplete.chip_autocomplete_restricted, chip_code=autocomplete.chipcode_autocomplete)
    async def request_chip(
        self,
        interaction: discord.Interaction,
        system: SupportedPlatformLiteral,
        game: SupportedGameLiteral,
        chip_name: str,
        chip_code: str,
    ) -> None:
        is_admin = interaction.user.guild_permissions.manage_messages
        await self._handle_request_chip(interaction, interaction.user, system, game, chip_name, chip_code, 0, is_admin)

    @requestfor_group.command(name="chip", description="Request a chip for someone")
    @app_commands.autocomplete(chip_name=autocomplete.chip_autocomplete_restricted, chip_code=autocomplete.chipcode_autocomplete)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def requestfor_chip(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        system: SupportedPlatformLiteral,
        game: SupportedGameLiteral,
        chip_name: str,
        chip_code: str,
        priority: Optional[int] = 0,
    ) -> None:
        await self._handle_request_chip(interaction, user, system, game, chip_name, chip_code, priority, True)

    async def _handle_request_chip(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        system: SupportedPlatformLiteral,
        game: SupportedGameLiteral,
        chip_name: str,
        chip_code: str,
        priority: int,
        is_admin: bool,
    ):
        try:
            if chip_code == "*":
                actual_chip_code = Code.Star
            else:
                actual_chip_code = Code[chip_code.upper()]
        except KeyError:
            await interaction.response.send_message(f"{Emotes.ERROR} That code isn't valid.", ephemeral=True)
            return

        chip = GAME_INFO[game].get_tradable_chip(chip_name, actual_chip_code)
        illegal_chip = GAME_INFO[game].get_illegal_chip(chip_name, actual_chip_code)
        if chip is None:
            chip = GAME_INFO[game].get_chip(chip_name, actual_chip_code)
            if chip is None:
                error = f"{Emotes.ERROR} That's not a valid chip."
            else:
                error = f"{Emotes.ERROR} {chip} cannot be traded in-game."
            await interaction.response.send_message(error, ephemeral=True)
            return
        elif illegal_chip is not None:
            await interaction.response.send_message(
                f"{Emotes.ERROR} {chip} is not obtainable in-game, so it cannot be requested.", ephemeral=True
            )
            return
        existing = await self.request(interaction, user, system, game, chip, priority, is_admin)
        if existing is None:
            await interaction.response.send_message(
                f"{Emotes.OK} Your request for `{chip}` has been added to the queue."
            )
        else:
            await interaction.response.send_message(
                f"{Emotes.ERROR} You are already in queue for `{existing.trade_item}`"
            )

    @request_group.command(name="ncp", description="Request a NaviCust part")
    @app_commands.autocomplete(part_name=autocomplete.ncp_autocomplete_restricted, part_color=autocomplete.ncpcolor_autocomplete)
    async def request_ncp(
        self,
        interaction: discord.Interaction,
        system: SupportedPlatformLiteral,
        game: SupportedGameLiteral,
        part_name: str,
        part_color: str,
    ) -> None:
        is_admin = interaction.user.guild_permissions.manage_messages
        await self._handle_request_ncp(interaction, interaction.user, system, game, part_name, part_color, 0, is_admin)

    @requestfor_group.command(name="ncp", description="Request a NaviCust part for someone")
    @app_commands.autocomplete(part_name=autocomplete.ncp_autocomplete_restricted, part_color=autocomplete.ncpcolor_autocomplete)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def requestfor_ncp(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        system: SupportedPlatformLiteral,
        game: SupportedGameLiteral,
        part_name: str,
        part_color: str,
        priority: Optional[int] = 0,
    ) -> None:
        await self._handle_request_ncp(interaction, user, system, game, part_name, part_color, priority, True)

    async def _handle_request_ncp(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        system: SupportedPlatformLiteral,
        game: SupportedGameLiteral,
        part_name: str,
        part_color: str,
        priority: int,
        is_admin: bool,
    ) -> None:
        try:
            actual_color = GAME_INFO[game].get_color(part_color)
        except KeyError:
            await interaction.response.send_message(
                f'{Emotes.ERROR} "{part_color}" is not a valid color in BN{game}.', ephemeral=True
            )
            return

        ncp = GAME_INFO[game].get_part(part_name, actual_color)
        if ncp is None:
            await interaction.response.send_message(f"{Emotes.ERROR} That's not a valid part.", ephemeral=True)
            return

        if ncp not in GAME_INFO[game].tradable_parts:
            await interaction.response.send_message(f"{Emotes.ERROR} {ncp} is not tradable.", ephemeral=True)
            return

        existing = await self.request(interaction, user, system, game, ncp, priority, is_admin)
        if existing is None:
            await interaction.response.send_message(
                f"{Emotes.OK} Your request for `{ncp}` has been added to the queue."
            )
        else:
            await interaction.response.send_message(
                f"{Emotes.ERROR} You are already in queue for {existing.trade_item}"
            )

    async def request(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        system: SupportedPlatformLiteral,
        game: SupportedGameLiteral,
        trade_item: TradeItem,
        priority: int,
        is_admin: bool,
    ) -> Optional[TradeRequest]:
        _, _, queued_users = self.trade_request_rpc_client.get_current_queue()
        if user.id in queued_users and not is_admin:
            return queued_users[user.id]
        await self.trade_request_rpc_client.submit_trade_request(
            user.display_name, user.id, interaction.channel_id, system.lower(), game, trade_item, priority
        )
        return None

    @app_commands.command(description="Show the queue for pending trades")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction):
        embed = self._make_queue_embed(interaction.user)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command()
    @owner_only()
    @app_commands.guild_only()
    async def clearqueue(self, interaction: discord.Interaction):
        await self.trade_request_rpc_client.clear_queue()
        await interaction.response.send_message(content=f"Queue cleared.")

    @app_commands.command()
    @owner_only()
    @app_commands.guild_only()
    async def resetuser(self, interaction: discord.Interaction, user: discord.User):
        current_queue, in_progress, queued_users = self.trade_request_rpc_client.get_current_queue()

        for cid, request in current_queue:
            if request.user_id == user.id:
                try:
                    self.trade_request_rpc_client.cached_queue.pop(cid)
                except KeyError:
                    pass
        for cid, response in in_progress:
            if response.request.user_id == user.id:
                try:
                    self.trade_request_rpc_client.in_progress.pop(cid)
                except KeyError:
                    pass
        try:
            self.trade_request_rpc_client.queued_users.pop(user.id)
        except KeyError:
            pass
        self.trade_request_rpc_client.save_queue()
        await interaction.response.send_message(content=f"User removed.")

    @app_commands.command()
    @owner_only()
    @app_commands.guild_only()
    async def togglegame(self, interaction: discord.Interaction, system: str, game: int, state: bool):
        await self.trade_request_rpc_client.set_game_enabled(system, game, state)
        await interaction.response.send_message(content=f"Trading for {system}/bn{game}: {state}")

    @app_commands.command()
    @owner_only()
    @app_commands.guild_only()
    async def toggleworker(self, interaction: discord.Interaction, worker_name: str, state: bool):
        await self.trade_request_rpc_client.set_worker_enabled(worker_name, state)
        await interaction.response.send_message(content=f"{worker_name} state: {state}")

    @toggleworker.autocomplete("worker_name")
    async def _autocomplete_worker_name(self, interaction: discord.Interaction, current: str):
        msgs = self.trade_request_rpc_client.cached_messages
        workers = set()
        for key in msgs.keys():
            match = re.match(r"worker/([A-Za-z0-9_-]+)/available", key)
            if match:
                worker_id = match.group(1)
                workers.add(worker_id)

        return [
            app_commands.Choice(name=worker_id, value=worker_id)
            for worker_id in sorted(workers)
            if worker_id.startswith(current)
        ]

    @app_commands.command()
    @app_commands.guild_only()
    async def toptrades(self, interaction: discord.Interaction):
        top_items = self.bot_stats.get_trades_by_trade_count()
        lines = []
        count = 0
        for trade_item, qty in top_items:
            count += 1
            if count > 20:
                break
            lines.append(f"{count}. {trade_item} x{qty}")

        embed = discord.Embed(title="Top trades")
        embed.add_field(name="Top trades", value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command()
    @app_commands.guild_only()
    async def topusers(self, interaction: discord.Interaction):
        top_users = self.bot_stats.get_users_by_trade_count()

        lines = []
        count = 0
        for user in top_users:
            count += 1
            if count >= 20:
                break
            discord_user = await self.bot.fetch_user(user.user_id)
            if discord_user is not None:
                lines.append(
                    f"{count}. {discord_user.display_name or discord_user.name or discord_user.id} - {user.get_total_trade_count()} trades"
                )
            else:
                if discord_user is not None:
                    lines.append(f"{count}. {user.user_id} - {user.get_total_trade_count()} trades")

        embed = discord.Embed(title="Top users by trade count")
        embed.add_field(name="Top users", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"{len(top_users)} users total")
        await interaction.response.send_message(embed=embed)

    @app_commands.command()
    @app_commands.guild_only()
    async def trades(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        if member is None:
            user_id = interaction.user.id
        else:
            user_id = member.id
        user = self.bot_stats.users.get(user_id)
        if user is None:
            if member is None:
                await interaction.response.send_message("You haven't made any trades.")
            else:
                await interaction.response.send_message(f"{member.display_name} hasn't made any trades.")
            return

        discord_user = member or interaction.user
        lines = []
        count = 0
        for chip, qty in user.get_trades_by_trade_count():
            count += 1
            if count >= 20:
                break
            lines.append(f"{count}. {chip.name} {chip.code} x{qty}")

        embed = discord.Embed(title=f"{discord_user.display_name}'s trades")
        embed.add_field(name="Top trades", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"{user.get_total_trade_count()} trades for {len(user.trades)} different things")
        await interaction.response.send_message(embed=embed)

    @app_commands.command()
    @app_commands.guild_only()
    async def tradecount(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"I've recorded trades for {self.bot_stats.get_total_trade_count()} things to {self.bot_stats.get_total_user_count()} users."
        )

    @app_commands.command()
    @app_commands.guild_only()
    async def listworkers(self, interaction: discord.Interaction):
        msgs = self.trade_request_rpc_client.cached_messages
        _, in_progress, _ = self.trade_request_rpc_client.get_current_queue()
        worker_to_trade_map: Dict[str, TradeResponse] = {trade[1].worker_id: trade[1] for trade in in_progress}

        lines = []
        for key in msgs.keys():
            match = re.match(r"worker/([A-Za-z0-9_-]+)/available", key)
            if match:
                worker_id = match.group(1)
                worker_hostname = msgs[f"worker/{worker_id}/hostname"].decode("utf-8")
                worker_system = (
                    Emotes.STEAM if msgs[f"worker/{worker_id}/system"].decode("utf-8") == "steam" else Emotes.SWITCH
                )
                worker_game = msgs[f"worker/{worker_id}/game"].decode("utf-8")
                worker_enabled = bool(msgs.get(f"worker/{worker_id}/enabled") == b"1")
                worker_available = bool(msgs[key] == b"1")

                if worker_enabled and worker_available:
                    if worker_id in worker_to_trade_map:
                        trade = worker_to_trade_map[worker_id]
                        status = f"trading: <@{trade.request.user_id}> - {trade.request.trade_item}"
                    else:
                        status = "idle"
                    emote = Emotes.OK
                elif worker_available and not worker_enabled:
                    emote = Emotes.WARNING
                    status = "disabled"
                else:
                    emote = Emotes.ERROR
                    status = "offline"
                lines.append(
                    f"{emote} {worker_hostname} ({worker_id[:8]}) - {worker_system} BN{worker_game} ({status})"
                )
        embed = discord.Embed(title=f"List of workers ({len(lines)})", description="\n".join(lines))
        await interaction.response.send_message(embed=embed)

    """
    @commands.command()
    @in_channel
    @commands.is_owner()
    async def control(self, ctx: commands.Context, buttons: str):
        input_list = buttons.split(",")
        converted_inputs = []

        all_buttons = {e.name.lower() for e in Button}
        all_dpads = {e.name.lower() for e in DPad}
        for x in input_list:
            lower_x = x.lower()
            if lower_x in all_dpads:
                converted_inputs.append(DPad[x.lower().capitalize()])
            elif lower_x in all_buttons:
                if lower_x == "zl" or lower_x == "zr":
                    x = x.upper()
                else:
                    x = x.lower().capitalize()
                converted_inputs.append(Button[x])
            else:
                await ctx.message.reply(f"Unknown input: {x}")
                return

        self.trade_manager.send_inputs(converted_inputs)
        await ctx.message.reply(f"Queued the inputs for execution")
    """

    """
    @commands.command()
    @in_channel
    @commands.is_owner()
    async def pause(self, ctx: commands.Context):
        self.trade_manager.request_queue.put(PauseQueueCommand(DiscordContext.create(ctx)))
    """

    """
    @commands.command()
    @commands.is_owner()
    async def screencapture(self, ctx: commands.Context):
        self.trade_manager.request_queue.put(ScreenCaptureCommand(DiscordContext.create(ctx)))
    """

    """
    @commands.command()
    @in_channel
    async def cancel(self, ctx: commands.Context, user_id: Optional[int] = None):
        if not await self.bot.is_owner(ctx.author):
            await ctx.message.add_reaction(MessageReaction.ERROR.value)
            await ctx.message.reply("This command is temporarily disabled.")
            return

        if user_id is not None:
            if not await self.bot.is_owner(ctx.author):
                await ctx.message.add_reaction(MessageReaction.ERROR.value)
                await ctx.message.reply("You can only cancel your own requests.")
            else:
                self.trade_manager.request_queue.put(
                    CancelCommand(DiscordContext(ctx.author.display_name, user_id, ctx.message.id, ctx.channel.id))
                )
        else:
            self.trade_manager.request_queue.put(CancelCommand(DiscordContext.create(ctx)))
    """


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TradeCog(bot))
