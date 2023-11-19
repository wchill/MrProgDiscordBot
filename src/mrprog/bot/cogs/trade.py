import asyncio
import atexit
import collections
import io
import json
import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.app_commands import AppCommandError
from discord.ext import commands, tasks
from mmbn.gamedata.bn3.bn3_chip import BN3Chip
from mmbn.gamedata.bn3.bn3_ncp_list import BN3NaviCustPartColor
from mmbn.gamedata.bn6.bn6_chip import BN6Chip
from mmbn.gamedata.bn6.bn6_ncp_list import BN6NaviCustPartColor
from mmbn.gamedata.chip import Code
from mmbn.gamedata.navicust_part import NaviCustPart, NaviCustColors
from mrprog.bot.supported_games import (
    SupportedGameLiteral,
    SupportedPlatformLiteral,
    CHIP_LISTS,
    NCP_LISTS,
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

        self.trade_request_rpc_client: TradeRequestRpcClient
        self.bot_stats = None

        self.channel_ids = set()
        self.queue_message = None
        self.worker_message = None

        self.time_since_last_update = 0

    @tasks.loop(seconds=180)
    async def change_status(self):
        try:
            if self.bot.is_ready():
                await self.bot.change_presence(
                    status=discord.Status.online,
                    activity=discord.Game(
                        name=f"{self.bot_stats.get_total_trade_count()} trades to "
                        f"{self.bot_stats.get_total_user_count()} users",
                    ),
                )
        except Exception:
            import traceback
            traceback.print_exc()

    @tasks.loop(seconds=1)
    async def update_queue_and_worker_status(self):
        try:
            t = time.time()
            if self.bot.is_ready():
                if self.trade_request_rpc_client.queue_modified and not self.bot.is_ws_ratelimited() and (t - self.time_since_last_update > 1):
                    embed = self._make_queue_embed()
                    await self.queue_message.edit(content="", embed=embed)
                    self.trade_request_rpc_client.queue_modified = False
                    self.time_since_last_update = t

                if self.trade_request_rpc_client.worker_status_modified and not self.bot.is_ws_ratelimited() and (t - self.time_since_last_update > 1):
                    embed = self._make_worker_embed(user_requested=False)
                    await self.worker_message.edit(content="", embed=embed)
                    self.trade_request_rpc_client.worker_status_modified = False
                    self.time_since_last_update = t
        except Exception:
            import traceback
            traceback.print_exc()

    async def cog_load(self) -> None:
        self.bot_stats = BotTradeStats.load_or_default("bot_stats.pkl")
        self.change_status.start()
        self.update_queue_and_worker_status.start()

        self.trade_request_rpc_client = TradeRequestRpcClient(
            "bn-orchestrator", "worker", "worker", self.message_room_code, self.handle_trade_update
        )

        await self.trade_request_rpc_client.connect()
        config_bytestring = await self.trade_request_rpc_client.wait_for_message("bot/config")
        config = json.loads(config_bytestring.decode("utf-8"))

        channel = await self.bot.fetch_channel(int(config["status_channel"]))
        queue_embed = self._make_queue_embed()
        worker_embed = self._make_worker_embed()

        if config.get("queue_message_id") is not None:
            try:
                queue_message_id = int(config["queue_message_id"])
                self.queue_message = await channel.fetch_message(queue_message_id)
                await self.queue_message.edit(content="", embed=queue_embed)
            except Exception:
                self.queue_message = None

        if self.queue_message is None:
            self.queue_message = await channel.send(embed=queue_embed)
            config["queue_message_id"] = str(self.queue_message.id)
            await self.trade_request_rpc_client.publish_retained_message("bot/config", json.dumps(config))

        if config.get("worker_message_id") is not None:
            try:
                worker_message_id = int(config["worker_message_id"])
                self.worker_message = await channel.fetch_message(worker_message_id)
                await self.worker_message.edit(content="", embed=worker_embed)
            except Exception:
                self.worker_message = None

        if self.worker_message is None:
            self.worker_message = await channel.send(embed=worker_embed)
            config["worker_message_id"] = str(self.worker_message.id)
            await self.trade_request_rpc_client.publish_retained_message("bot/config", json.dumps(config))

        atexit.register(self.atexit_func)
        logger.debug("Trade cog successfully loaded")

    async def cog_unload(self) -> None:
        atexit.unregister(self.atexit_func)
        self.change_status.stop()
        self.update_queue.stop()
        self.update_workers.stop()
        await self.trade_request_rpc_client.disconnect()
        self.bot_stats.save("bot_stats.pkl")

    def atexit_func(self) -> None:
        asyncio.run(self.trade_request_rpc_client.disconnect())
        self.bot_stats.save("bot_stats.pkl")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: AppCommandError):
        import traceback

        logger.exception("\n".join(traceback.format_exception(error)))

    def _make_queue_embed(self, requested_user: Optional[discord.User] = None) -> discord.Embed:
        queued: List[Tuple[int, TradeRequest]]
        in_progress: List[Tuple[str, TradeRequest]]
        queued, in_progress, _ = self.trade_request_rpc_client.get_current_queue()

        fields: Dict[Tuple[str, int], List[Tuple[int, int, int, TradeItem]]] = collections.defaultdict(list)
        for correlation_id, request in queued:
            trades = fields[(request.system, request.game)]
            count = len(trades)

            if count >= 20:
                continue

            trades.append((request.priority, count, request.user_id, request.trade_item))

        embed = discord.Embed(title=f"Current queue ({len(queued)})")

        for (system, game), trades in sorted(fields.items(), key=lambda kv: kv[0]):
            trades_sorted = sorted(trades, key=lambda trade: (trade[0], trade[1]))
            lines = []
            for position, (priority, _, user_id, trade_item) in enumerate(trades_sorted):
                if priority > 0:
                    lines.append(f"{position}. <@{user_id}> - `{trade_item}` (priority {priority})")
                else:
                    lines.append(f"{position}. <@{user_id}> - `{trade_item}`")
            system_emote = Emotes.STEAM if system == "steam" else Emotes.SWITCH
            embed.add_field(name=f"{system_emote} BN{game} ({len(lines)})", value="\n".join(lines))

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

        return embed

    def _make_worker_embed(self, user_requested: bool = False) -> discord.Embed:
        in_progress: List[Tuple[str, TradeRequest]]

        msgs = self.trade_request_rpc_client.cached_messages
        _, in_progress, _ = self.trade_request_rpc_client.get_current_queue()
        worker_to_trade_map: Dict[str, TradeRequest] = {worker_id: request for worker_id, request in in_progress}

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
                worker_enabled = msgs.get(f"worker/{worker_id}/enabled") == b"1"
                worker_available = msgs[key] == b"1"

                if worker_enabled and worker_available:
                    if worker_id in worker_to_trade_map:
                        trade = worker_to_trade_map[worker_id]
                        status = f"trading: <@{trade.user_id}> - `{trade.trade_item}`"
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

        worker_count = len(lines)
        global_enable = msgs.get("bot/enabled", b"0") == b"1"
        lines.append("")
        if global_enable:
            lines.append(f"{Emotes.OK} trades currently being processed")
        else:
            lines.append(f"{Emotes.ERROR} trades currently not being processed")
        embed = discord.Embed(title=f"List of workers ({worker_count})", description="\n".join(lines))

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
                        content = f"{emote} <@{trade_response.request.user_id}>: {trade_response.message}\n" \
                                  f"Restarting worker and retrying trade. (Worker id {trade_response.worker_id})"
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
        try:
            await user.send(
                f"Your `{trade_response.request.trade_item}` is ready! You have 3 minutes to join before the trade is cancelled.",
                silent=False,
                file=discord.File(fp=io.BytesIO(trade_response.image), filename="roomcode.png"),
            )
        except discord.errors.Forbidden:
            channel = await self.bot.fetch_channel(trade_response.request.channel_id)
            await channel.send(
                f"{Emotes.ERROR} <@{trade_response.request.user_id}>: I am unable to send DMs to you. "
                f"Please enable DMs so I can send you the trade code. Skipping trade."
            )

    requestfor_group = app_commands.Group(name="requestfor", description="...")

    @request_group.command(name="chip", description="Request a chip")
    @app_commands.autocomplete(
        chip_name=autocomplete.chip_autocomplete_restricted, chip_code=autocomplete.chipcode_autocomplete
    )
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
    @app_commands.autocomplete(
        chip_name=autocomplete.chip_autocomplete_restricted, chip_code=autocomplete.chipcode_autocomplete
    )
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

        chip = CHIP_LISTS[game].get_tradable_chip(chip_name, actual_chip_code)
        illegal_chip = CHIP_LISTS[game].get_unobtainable_chip(chip_name, actual_chip_code)
        if chip is None:
            chip = CHIP_LISTS[game].get_chip(chip_name, actual_chip_code)
            if chip is None:
                error = f"{Emotes.ERROR} That's not a valid chip."
            else:
                error = f"{Emotes.ERROR} `{chip}` cannot be traded in-game."
            await interaction.response.send_message(error, ephemeral=True)
            return
        elif illegal_chip is not None:
            await interaction.response.send_message(
                f"{Emotes.ERROR} `{chip}` is not obtainable in-game, so it cannot be requested.", ephemeral=True
            )
            return

        messages = self.trade_request_rpc_client.cached_messages
        if messages.get(f"game/{system.lower()}/{game}/enabled", "0") == "0":
            await interaction.response.send_message(
                f"{Emotes.ERROR} Trading is currently disabled for this game on this platform."
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
    @app_commands.autocomplete(
        part_name=autocomplete.ncp_autocomplete_restricted, part_color=autocomplete.ncpcolor_autocomplete
    )
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
    @app_commands.autocomplete(
        part_name=autocomplete.ncp_autocomplete_restricted, part_color=autocomplete.ncpcolor_autocomplete
    )
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
            actual_color = NaviCustColors[part_color]
        except KeyError:
            await interaction.response.send_message(
                f'{Emotes.ERROR} "{part_color}" is not a valid color in BN{game}.', ephemeral=True
            )
            return

        ncp = NCP_LISTS[game].get_part(part_name, actual_color)
        if ncp is None:
            await interaction.response.send_message(f"{Emotes.ERROR} That's not a valid part.", ephemeral=True)
            return

        if ncp not in NCP_LISTS[game].tradable_parts:
            await interaction.response.send_message(f"{Emotes.ERROR} `{ncp}` cannot be traded in-game.", ephemeral=True)
            return

        if ncp in NCP_LISTS[game].unobtainable_parts:
            await interaction.response.send_message(
                f"{Emotes.ERROR} `{ncp}` is not obtainable in-game, so it cannot be requested.", ephemeral=True
            )
            return

        messages = self.trade_request_rpc_client.cached_messages
        if messages.get(f"game/{system.lower()}/{game}/enabled", "0") == "0":
            await interaction.response.send_message(
                f"{Emotes.ERROR} Trading is currently disabled for this game on this platform."
            )
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
        # TODO: Block requests if the requested system/game combo is not online
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
    async def togglegame(self, interaction: discord.Interaction, system: str, game: int, state: bool):
        await self.trade_request_rpc_client.set_game_enabled(system, game, state)
        await interaction.response.send_message(content=f"Trading for {system}/bn{game}: {state}")

    @app_commands.command()
    @owner_only()
    @app_commands.guild_only()
    async def toggleworker(self, interaction: discord.Interaction, worker_id: str, state: bool):
        await self.trade_request_rpc_client.set_worker_enabled(worker_id, state)
        await interaction.response.send_message(content=f"{worker_id} state: {state}")

    @app_commands.command()
    @owner_only()
    @app_commands.guild_only()
    async def togglebot(self, interaction: discord.Interaction, state: bool):
        await self.trade_request_rpc_client.set_bot_enabled(state)
        await interaction.response.send_message(content=f"state: {state}")

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
            game = None
            if isinstance(trade_item, BN3Chip):
                game = 3
            elif isinstance(trade_item, BN6Chip):
                game = 6
            elif isinstance(trade_item, NaviCustPart):
                if isinstance(trade_item.color, BN3NaviCustPartColor):
                    game = 3
                elif isinstance(trade_item.color, BN6NaviCustPartColor):
                    game = 6

            if game is None:
                raise RuntimeError(f"Unknown game: {trade_item}")

            lines.append(f"{count}. `{trade_item}` (BN{game}) x{qty}")

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
            if count > 20:
                break
            lines.append(f"{count}. <@{user.user_id}> - {user.get_total_trade_count()} trades")

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
        embed = self._make_worker_embed(user_requested=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command()
    @app_commands.guild_only()
    async def workerstatus(self, interaction: discord.Interaction, worker_id: str):
        msgs = self.trade_request_rpc_client.cached_messages
        _, in_progress, _ = self.trade_request_rpc_client.get_current_queue()

        worker_hostname = msgs[f"worker/{worker_id}/hostname"].decode("utf-8")
        worker_system_name = msgs[f"worker/{worker_id}/system"].decode("utf-8")
        worker_system_emote = Emotes.STEAM if worker_system_name == "steam" else Emotes.SWITCH
        worker_game = msgs[f"worker/{worker_id}/game"].decode("utf-8")
        worker_enabled = bool(msgs.get(f"worker/{worker_id}/enabled") == b"1")
        worker_available = bool(msgs[f"worker/{worker_id}/available"] == b"1")
        worker_versions = json.loads(msgs[f"worker/{worker_id}/version"].decode("utf-8"))
        git_version_str = "\n".join([f"{item[0]}: `{item[1]}`" for item in worker_versions.items()])

        if worker_enabled and worker_available:
            if msgs.get(f"worker/{worker_id}/current_trade"):
                trade_request = TradeRequest.from_bytes(msgs.get(f"worker/{worker_id}/current_trade"))
                status = f"Online, trading: <@{trade_request.user_id}> - {trade_request.trade_item}"
                status_emote = Emotes.OK
            else:
                status = "Online, idle"
                status_emote = Emotes.OK
        elif worker_available and not worker_enabled:
            status = "Online, disabled"
            status_emote = Emotes.WARNING
        else:
            status = "Offline"
            status_emote = Emotes.ERROR

        embed = discord.Embed(title=f"Worker status")
        embed.add_field(name="Worker ID", value=worker_id)
        embed.add_field(name="Hostname", value=worker_hostname)
        embed.add_field(name="System", value=f"{worker_system_emote} {worker_system_name.capitalize()}")
        embed.add_field(name="Game", value=f"Battle Network {worker_game}")
        embed.add_field(name="Status", value=f"{status_emote} {status}")
        embed.add_field(name="Version", value=git_version_str)

        await interaction.response.send_message(embed=embed)

    @toggleworker.autocomplete("worker_id")
    @workerstatus.autocomplete("worker_id")
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

    @app_commands.command()
    @app_commands.guild_only()
    async def cancel(self, interaction: discord.Interaction):
        _, _, queued_by_user = self.trade_request_rpc_client.get_current_queue()
        if interaction.user.id not in queued_by_user:
            await interaction.response.send_message(
                f"{Emotes.ERROR} You are not in the queue."
            )
            return

        await self.trade_request_rpc_client.cancel_trade_request(interaction.user.id)
        await interaction.response.send_message(
            f"{Emotes.OK} Your request has been cancelled."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TradeCog(bot))
