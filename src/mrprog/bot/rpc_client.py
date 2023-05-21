import asyncio
import logging
import platform
import uuid
from typing import Dict, List, MutableMapping, Optional, Tuple

import aio_pika
import asyncio_mqtt
import psutil
from aio_pika import Message
from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractIncomingMessage,
    AbstractQueue,
    AbstractRobustConnection,
)
from mrprog.utils.supported_games import SUPPORTED_GAMES
from mrprog.utils.trade import TradeRequest, TradeResponse
from mrprog.utils.types import TradeItem

logger = logging.getLogger(__name__)


# noinspection PyTypeChecker
class TradeRequestRpcClient:
    mqtt_client: asyncio_mqtt.Client

    amqp_connection: AbstractRobustConnection
    channel: AbstractChannel
    task_queues: Dict[Tuple[str, int], AbstractQueue]
    notification_queue: AbstractQueue
    loop: asyncio.AbstractEventLoop
    exchange: AbstractExchange

    def __init__(self, host: str, username: str, password: str):
        self.loop = asyncio.get_running_loop()
        self.futures: MutableMapping[str, Tuple[asyncio.Future, asyncio.Future]] = {}

        self.request_counter = 0
        self.cached_queue: Dict[int, TradeRequest] = {}
        self.in_progress: Dict[int, TradeResponse] = {}

        self._amqp_connection_str = f"amqp://{username}:{password}@{host}/"
        self._mqtt_connection_info = (host, username, password)

        self._mqtt_update_task = None
        self.task_queues = {}

    async def handle_mqtt_updates(self) -> None:
        async with self.mqtt_client.messages() as messages:
            await self.mqtt_client.subscribe("worker/+/available")
            async for message in messages:
                print(message.payload)

    async def get_retained_message_from_topic(self, topic: str) -> str:
        async with self.mqtt_client.messages() as messages:
            await self.mqtt_client.subscribe(topic)
            async for message in messages:
                return message.payload

    async def publish_retained_message(self, topic: str, message: str) -> None:
        await self.mqtt_client.publish(topic=topic, payload=message, qos=1, retain=True)

    async def update_mqtt_info(self) -> None:
        interfaces = psutil.net_if_addrs()
        ip_address = None
        for interface in interfaces:
            for address in interfaces[interface]:
                if address.address.startswith("100."):
                    ip_address = address.address
                    break

        await self.mqtt_client.publish(topic="bot/hostname", payload=platform.node(), qos=1, retain=True)
        await self.mqtt_client.publish(topic="bot/address", payload=ip_address, qos=1, retain=True)
        await self.mqtt_client.publish(topic="bot/available", payload="1", qos=1, retain=True)

        self._mqtt_update_task = self.loop.create_task(self.handle_mqtt_updates())

        self.request_counter = int(await self.get_retained_message_from_topic("bot/trade_id"))

    async def connect(self):
        mqtt_host, mqtt_user, mqtt_pass = self._mqtt_connection_info
        self.mqtt_client = asyncio_mqtt.Client(
            hostname=mqtt_host,
            username=mqtt_user,
            password=mqtt_pass,
            will=asyncio_mqtt.Will(topic="bot/available", payload="0", qos=1, retain=True),
            clean_session=True,
        )
        await self.mqtt_client.connect()
        await self.update_mqtt_info()

        self.amqp_connection = await aio_pika.connect_robust(
            self._amqp_connection_str,
            loop=self.loop,
        )
        self.channel = await self.amqp_connection.channel()

        # Declare an exchange
        self.exchange = await self.channel.declare_exchange(
            name="trade_requests",
            type=aio_pika.ExchangeType.TOPIC,
        )

        # Declaring queues
        for system in SUPPORTED_GAMES:
            for game in SUPPORTED_GAMES[system]:
                task_queue = await self.channel.declare_queue(
                    name=f"{system}_bn{game}_task_queue", durable=True, arguments={"x-max-priority": 100}
                )
                await task_queue.bind(self.exchange, routing_key=f"requests.{system}.bn{game}")
                self.task_queues[(system, game)] = task_queue

        self.notification_queue = await self.channel.declare_queue(name="trade_status_update", durable=True)
        await self.notification_queue.bind(self.exchange, routing_key=self.notification_queue.name)
        await self.notification_queue.consume(self.on_trade_update)

    async def on_trade_update(self, message: AbstractIncomingMessage) -> None:
        async with message.process():
            if message.correlation_id is None:
                logger.warning(f"Bad message {message!r}")
                return
            elif message.correlation_id not in self.futures:
                logger.warning(f"No futures for {message.correlation_id!r}")
                return

            logger.debug(f"Received message {message.correlation_id}")

            f1, f2 = self.futures.get(message.correlation_id)

            response = TradeResponse.from_bytes(message.body)
            if response.status == TradeResponse.IN_PROGRESS:
                try:
                    self.cached_queue.pop(response.request.user_id)
                except KeyError:
                    logger.warning(f"Unable to find {response.request.user_id} in cached queue")
                self.in_progress[response.request.user_id] = response
                f1.set_result(response)
                return
            else:
                if not f1.done():
                    f1.cancel()
                f2.set_result(response)
                self.futures.pop(message.correlation_id)
                try:
                    self.in_progress.pop(response.request.user_id)
                except KeyError:
                    logger.warning(f"Unable to find {response.request.user_id} in progress dict")

    async def submit_trade_request(
        self,
        user_name: str,
        user_id: int,
        channel_id: int,
        system: str,
        game: int,
        trade_item: TradeItem,
        priority: Optional[int] = 0,
    ) -> Tuple[asyncio.Future, asyncio.Future]:
        correlation_id = str(uuid.uuid4())

        room_code_future = self.loop.create_future()
        trade_finish_future = self.loop.create_future()
        self.futures[correlation_id] = (room_code_future, trade_finish_future)

        trade_request = TradeRequest(
            user_name, user_id, channel_id, system, game, self.request_counter, trade_item, priority
        )
        self.cached_queue[user_id] = trade_request

        await self.exchange.publish(
            Message(
                body=trade_request.to_bytes(),
                content_type="application/json",
                correlation_id=correlation_id,
                reply_to=self.notification_queue.name,
                priority=priority,
            ),
            routing_key=f"requests.{system}.bn{game}",
        )
        self.request_counter += 1
        await self.mqtt_client.publish(topic="bot/trade_id", payload=self.request_counter, qos=1, retain=True)

        return self.futures[correlation_id]

    def get_current_queue(self) -> Tuple[List[Tuple[int, TradeRequest]], List[Tuple[int, TradeResponse]]]:
        return list(self.cached_queue.items()), list(self.in_progress.items())

    async def clear_queue(self) -> None:
        for key, task_queue in self.task_queues.items():
            await task_queue.purge()
        self.cached_queue.clear()

    async def set_game_enabled(self, system: str, game: int, enabled: bool) -> None:
        await self.mqtt_client.publish(
            topic=f"enabled/{system}/bn{game}", payload="1" if enabled else "0", qos=1, retain=True
        )

    async def disconnect(self) -> None:
        self._mqtt_update_task.cancel()
        try:
            await self._mqtt_update_task
        except asyncio.CancelledError:
            pass
        await self.amqp_connection.close()
        await self.mqtt_client.disconnect()
