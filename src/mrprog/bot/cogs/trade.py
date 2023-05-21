import asyncio
import io
import json
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from mmbn.gamedata.chip import Code
from mmbn.gamedata.navicust_part import ColorLiteral
from mrprog.utils.supported_games import GAME_INFO, SupportedGameLiteral
from mrprog.utils.types import TradeItem

from mrprog.bot import autocomplete
from mrprog.bot.rpc_client import TradeRequestRpcClient, TradeResponse
from mrprog.bot.stats.trade_stats import BotTradeStats
from mrprog.bot.utils import Emotes

CHANNEL_IDS = {1109759453147967529}


def in_channel_check(ctx: commands.Context):
    return ctx.channel.id in CHANNEL_IDS


in_channel = commands.check(in_channel_check)


class TradeCog(commands.Cog, name="Trade"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.trade_request_rpc_client = TradeRequestRpcClient("bn-orchestrator", "worker", "worker")
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

    async def cog_load(self) -> None:
        self.bot_stats = BotTradeStats.load_or_default("bot_stats.pkl")
        self.change_status.start()

        await self.trade_request_rpc_client.connect()
        config = json.loads(await self.trade_request_rpc_client.get_retained_message_from_topic("bot/config"))

        channel = await self.bot.fetch_channel(config["queue_message_channel"])
        if config["queue_message_id"] is not None:
            try:
                queue_message_id = int(config["queue_message_id"])
                self.queue_message = await channel.fetch_message(queue_message_id)
                return
            except Exception:
                pass
        else:
            embed = self._make_queue_embed()
            self.queue_message = await channel.send(embed=embed)
            config["queue_message_id"] = str(self.queue_message.id)

            await self.trade_request_rpc_client.publish_retained_message("bot/config", json.dumps(config))

    async def cog_unload(self) -> None:
        await self.trade_request_rpc_client.disconnect()
        self.bot_stats.save("bot_stats.pkl")

    def _make_queue_embed(self, requested_user: Optional[discord.User] = None) -> discord.Embed:
        queued, in_progress = self.trade_request_rpc_client.get_current_queue()

        lines = []
        count = 0
        for user_id, request in queued:
            if count >= 30:
                break
            count += 1
            lines.append(f"{count}. {request.user_name} ({request.user_id}) - {request.trade_item}")
        embed = discord.Embed(
            title=f"Current queue ({len(queued)})",
            description="\n".join(lines) if lines else "No queued trades",
        )

        lines = []
        count = 0
        for user_id, response in in_progress:
            if count >= 30:
                break
            count += 1
            request = response.request
            lines.append(
                f"{count}. <@{request.user_id}> - (BN{request.game}) `{request.trade_item}` ({response.worker_id})"
            )
        embed.add_field(name="In progress", value="\n".join(lines) if lines else "No one")

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

    async def update_queue_message(self):
        embed = self._make_queue_embed()
        asyncio.run_coroutine_threadsafe(self.queue_message.edit(content="", embed=embed), self.bot.loop)

    async def handle_trade_complete(self, trade_response: TradeResponse):
        discord_channel = self.bot.get_channel(trade_response.request.channel_id)

        if trade_response.message or trade_response.embed:
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

    async def message_room_code(self, trade_response: TradeResponse):
        user = await self.bot.fetch_user(trade_response.request.user_id)
        await user.send(
            f"Your `{trade_response.request.trade_item}` is ready! You have 180 seconds to join",
            silent=False,
            file=discord.File(fp=io.BytesIO(trade_response.image), filename="roomcode.png"),
        )

    request_group = app_commands.Group(name="request", description="...")
    requestfor_group = app_commands.Group(name="requestfor", description="...")

    @request_group.command(name="chip")
    @app_commands.autocomplete(chip_name=autocomplete.chip_autocomplete)
    @app_commands.autocomplete(chip_code=autocomplete.chipcode_autocomplete)
    @in_channel
    async def request_chip(
        self, interaction: discord.Interaction, game: SupportedGameLiteral, chip_name: str, chip_code: str
    ) -> None:
        await self._handle_request_chip(interaction, interaction.user, game, chip_name, chip_code)

    @requestfor_group.command(name="chip")
    @app_commands.autocomplete(chip_name=autocomplete.chip_autocomplete)
    @app_commands.autocomplete(chip_code=autocomplete.chipcode_autocomplete)
    @commands.is_owner()
    @in_channel
    async def requestfor_chip(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        game: SupportedGameLiteral,
        chip_name: str,
        chip_code: str,
    ) -> None:
        await self._handle_request_chip(interaction, user, game, chip_name, chip_code)

    async def _handle_request_chip(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        game: SupportedGameLiteral,
        chip_name: str,
        chip_code: str,
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
        if chip is None:
            chip = GAME_INFO[game].get_chip(chip_name, actual_chip_code)
            if chip is None:
                error = f"{Emotes.ERROR} That's not a valid chip."
            else:
                error = f"{Emotes.ERROR} {chip} cannot be traded in-game."
            await interaction.response.send_message(error, ephemeral=True)
            return
        await self.request(interaction, user.name, user.id, game, chip)
        await interaction.response.send_message(f"{Emotes.OK} Your request for `{chip}` has been added to the queue.")

    @request_group.command(name="ncp")
    @app_commands.autocomplete(part_name=autocomplete.ncp_autocomplete)
    @app_commands.autocomplete(part_color=autocomplete.ncpcolor_autocomplete)
    @in_channel
    async def request_ncp(
        self, interaction: discord.Interaction, game: SupportedGameLiteral, part_name: str, part_color: ColorLiteral
    ) -> None:
        await self._handle_request_ncp(interaction, interaction.user, game, part_name, part_color)

    @requestfor_group.command(name="ncp")
    @app_commands.autocomplete(part_name=autocomplete.ncp_autocomplete)
    @app_commands.autocomplete(part_color=autocomplete.ncpcolor_autocomplete)
    @commands.is_owner()
    @in_channel
    async def requestfor_ncp(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        game: SupportedGameLiteral,
        part_name: str,
        part_color: ColorLiteral,
    ) -> None:
        await self._handle_request_ncp(interaction, user, game, part_name, part_color)

    async def _handle_request_ncp(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        game: SupportedGameLiteral,
        part_name: str,
        part_color: ColorLiteral,
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
        await self.request(interaction, user.name, user.id, game, ncp)
        await interaction.response.send_message(f"{Emotes.OK} Your request for `{ncp}` has been added to the queue.")

    async def request(
        self,
        interaction: discord.Interaction,
        user_name: str,
        user_id: int,
        game: SupportedGameLiteral,
        trade_item: TradeItem,
    ):
        ready, done = await self.trade_request_rpc_client.submit_trade_request(
            user_name, user_id, interaction.channel_id, "switch", game, trade_item
        )
        ready.add_done_callback(
            lambda fut: asyncio.run_coroutine_threadsafe(self.message_room_code(fut.result()), self.bot.loop)
        )
        done.add_done_callback(
            lambda fut: asyncio.run_coroutine_threadsafe(self.handle_trade_complete(fut.result()), self.bot.loop)
        )
        await self.update_queue_message()

    @app_commands.command()
    @in_channel
    async def queue(self, interaction: discord.Interaction):
        embed = self._make_queue_embed(interaction.user)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command()
    @commands.is_owner()
    async def clearqueue(self, interaction: discord.Interaction):
        await self.trade_request_rpc_client.clear_queue()
        await interaction.response.send_message(content=f"Queue cleared.")

    @app_commands.command()
    @commands.is_owner()
    async def togglegame(self, interaction: discord.Interaction, system: str, game: int, state: bool):
        await self.trade_request_rpc_client.set_game_enabled(system, game, state)
        await interaction.response.send_message(content=f"Trading for {system}/bn{game}: {state}")

    @app_commands.command()
    @in_channel
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
    @in_channel
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
    @in_channel
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
    @in_channel
    async def tradecount(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"I've recorded trades for {self.bot_stats.get_total_trade_count()} things to {self.bot_stats.get_total_user_count()} users."
        )

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
