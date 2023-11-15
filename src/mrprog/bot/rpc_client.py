import asyncio
import collections
import hashlib
import json
import logging
import platform
import re
import uuid
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple, Union

import aio_pika
import asyncio_mqtt
import jsonpickle
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


class WorkerStatus:
    def __init__(self):
        self._hostname = ""
        self._address = ""
        self._system = ""
        self._game = 0
        self._available = False
        self._enabled = False
        self._version = {}
        self._current_trade = None

    def update(self, topic: str, message: bytes):
        self.__setattr__(topic, message)
    
    @property
    def hostname(self) -> str:
        return self._hostname
    
    @hostname.setter
    def hostname(self, new_hostname: Union[bytes, str]) -> None:
        if isinstance(new_hostname, bytes):
            self._hostname = new_hostname.decode("utf-8")
        else:
            self._hostname = new_hostname
    
    @property
    def address(self) -> str:
        return self._address
    
    @address.setter
    def address(self, new_address: Union[bytes, str]) -> None:
        if isinstance(new_address, bytes):
            self._address = new_address.decode("utf-8")
        else:
            self._address = new_address
    
    @property
    def system(self) -> str:
        return self._system
    
    @system.setter
    def system(self, new_system: Union[bytes, str]) -> None:
        if isinstance(new_system, bytes):
            self._system = new_system.decode("utf-8")
        else:
            self._system = new_system
    
    @property
    def game(self) -> int:
        return self._game
    
    @game.setter
    def game(self, new_game: Union[bytes, int]) -> None:
        if isinstance(new_game, bytes):
            self._game = int(new_game.decode("utf-8"))
        else:
            self._game = new_game
    
    @property
    def available(self) -> bool:
        return self._available
    
    @available.setter
    def available(self, new_available: Union[bytes, bool]) -> None:
        if isinstance(new_available, bytes):
            self._available = new_available.decode("utf-8")
        else:
            self._available = new_available
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    @enabled.setter
    def enabled(self, new_enabled: Union[bytes, bool]) -> None:
        if isinstance(new_enabled, bytes):
            self._enabled = new_enabled.decode("utf-8")
        else:
            self._enabled = new_enabled
    
    @property
    def version(self) -> Dict[str, str]:
        return self._version
    
    @version.setter
    def version(self, new_version: Union[bytes, Dict[str, str]]) -> None:
        if isinstance(new_version, bytes):
            self._version = json.loads(new_version.decode("utf-8"))
        else:
            self._version = new_version
    
    @property
    def current_trade(self) -> TradeRequest:
        return self._current_trade
    
    @current_trade.setter
    def current_trade(self, new_current_trade: Union[bytes, TradeRequest]) -> None:
        if isinstance(new_current_trade, bytes):
            self._current_trade = TradeRequest.from_bytes(new_current_trade)
        else:
            self._current_trade = new_current_trade


# noinspection PyTypeChecker
class TradeRequestRpcClient:
    mqtt_client: asyncio_mqtt.Client

    amqp_connection: AbstractRobustConnection
    channel: AbstractChannel
    task_queues: Dict[Tuple[str, int], AbstractQueue]
    notification_queue: AbstractQueue
    loop: asyncio.AbstractEventLoop
    exchange: AbstractExchange

    def __init__(self, host: str, username: str, password: str, message_room_code_cb, handle_trade_complete_cb):
        self.loop = asyncio.get_running_loop()

        self.request_counter = 0
        self.cached_queue: Dict[str, TradeRequest] = {}
        self.queue_modified = False

        self.message_room_code_cb = message_room_code_cb
        self.handle_trade_update_cb = handle_trade_complete_cb

        self._amqp_connection_str = f"amqp://{username}:{password}@{host}/"
        self._mqtt_connection_info = (host, username, password)

        self._mqtt_update_task = None
        self.task_queues = {}

        self.topic_callbacks: Dict[str, Callable[[AbstractIncomingMessage], None]] = {}
        self.cached_messages = {}
        self.worker_statuses: Dict[str, WorkerStatus] = collections.defaultdict(WorkerStatus)
        self.worker_status_modified = False

    async def handle_mqtt_updates(self) -> None:
        async with self.mqtt_client.messages() as messages:
            await self.mqtt_client.subscribe("#", qos=1)
            async for message in messages:
                self.cached_messages[str(message.topic)] = message.payload
                for watched_topic in self.topic_callbacks.keys():
                    if message.topic.matches(watched_topic):
                        self.topic_callbacks[watched_topic](message)

    def handle_worker_updates(self, message: asyncio_mqtt.Message) -> None:
        match = re.match(r"worker/([A-Za-z0-9_-]+)/(.*)", str(message.topic))
        if not match:
            logger.warning(f"Unable to match topic: {message.topic}")
            return
        worker_id = match.group(1)
        topic = match.group(2)
        self.worker_statuses[worker_id].update(topic, message.payload)
        self.worker_status_modified = True

    async def wait_for_message(self, topic: str) -> bytes:
        if topic in self.cached_messages:
            return self.cached_messages[topic]
        else:
            future = asyncio.Future()
            self.topic_callbacks[topic] = lambda message: future.set_result(message.payload)
            result = await future
            self.topic_callbacks.pop(topic)
            return result

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

        self.topic_callbacks["worker/#"] = self.handle_worker_updates
        self._mqtt_update_task = self.loop.create_task(self.handle_mqtt_updates())

        message = await self.wait_for_message("bot/trade_id")
        self.request_counter = int(message.decode("utf-8"))

        await self.mqtt_client.publish(topic="bot/hostname", payload=platform.node(), qos=1, retain=True)
        await self.mqtt_client.publish(topic="bot/address", payload=ip_address, qos=1, retain=True)

    async def connect(self):
        mqtt_host, mqtt_user, mqtt_pass = self._mqtt_connection_info
        self.mqtt_client = asyncio_mqtt.Client(
            hostname=mqtt_host,
            username=mqtt_user,
            password=mqtt_pass,
            will=asyncio_mqtt.Will(topic="bot/available", payload="0", qos=1, retain=True),
            clean_session=True,
            client_id=hashlib.sha256(platform.node().encode("utf-8")).hexdigest(),
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
            name="trade_requests", type=aio_pika.ExchangeType.TOPIC, durable=True
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

        # Fetch saved messages from all task queues before starting
        await self.refresh_queue()

        await self.mqtt_client.publish(topic="bot/available", payload="1", qos=1, retain=True)

    async def refresh_queue(self, user_id: Optional[int] = None) -> int:
        self.cached_queue.clear()

        removed_messages = 0

        connection = await aio_pika.connect_robust(self._amqp_connection_str, loop=self.loop)
        channel = await connection.channel()
        
        for system in SUPPORTED_GAMES:
            for game in SUPPORTED_GAMES[system]:
                task_queue = await channel.get_queue(f"{system}_bn{game}_task_queue")

                while True:
                    try:
                        message = await task_queue.get(timeout=5)

                        request = TradeRequest.from_bytes(message.body)
                        if request.user_id == user_id:
                            await message.ack()
                            removed_messages += 1
                        else:
                            self.cached_queue[message.correlation_id] = message

                    except aio_pika.exceptions.QueueEmpty:
                        break

        await channel.close()
        await connection.close()

        logger.info(f"Retrieved {len(self.cached_queue)} messages")
        self.queue_modified = True
        return removed_messages

    async def on_trade_update(self, message: AbstractIncomingMessage) -> None:
        async with message.process():
            if message.correlation_id is None:
                logger.warning(f"Bad message {message!r}")
                return

            logger.info(f"Received message {message.correlation_id}")

            response = TradeResponse.from_bytes(message.body)
            if response.status == TradeResponse.IN_PROGRESS:
                if response.image is not None:
                    try:
                        self.cached_queue.pop(message.correlation_id)
                    except KeyError:
                        logger.warning(f"Unable to find {message.correlation_id} in cached queue")
                    await self.message_room_code_cb(response)
                else:
                    await self.handle_trade_update_cb(response)
            else:
                try:
                    self.cached_queue.pop(message.correlation_id)
                except KeyError:
                    pass
                await self.handle_trade_update_cb(response)

            self.queue_modified = True

    async def submit_trade_request(
        self,
        user_name: str,
        user_id: int,
        channel_id: int,
        system: str,
        game: int,
        trade_item: TradeItem,
        priority: Optional[int] = 0,
    ) -> None:
        correlation_id = str(uuid.uuid4())

        trade_request = TradeRequest(
            user_name, user_id, channel_id, system, game, self.request_counter, trade_item, priority
        )
        self.cached_queue[correlation_id] = trade_request
        self.queue_modified = True

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

    async def cancel_trade_request(self, user_id: int) -> bool:
        removed = await self.refresh_queue(user_id)
        return removed > 0

    def get_current_queue(
        self,
    ) -> Tuple[List[Tuple[int, TradeRequest]], List[Tuple[str, TradeRequest]], Dict[int, TradeRequest]]:
        in_progress = []
        for worker_id, status in self.worker_statuses.items():
            if status.current_trade:
                in_progress.append((worker_id, status.current_trade))

        return list(self.cached_queue.items()), in_progress, {request.user_id: request for request in self.cached_queue.values()}

    async def clear_queue(self) -> None:
        for key, task_queue in self.task_queues.items():
            await task_queue.purge()
        self.cached_queue.clear()
        self.queue_modified = True

    async def set_game_enabled(self, system: str, game: int, enabled: bool) -> None:
        logger.info(f"Setting game {system} {game} to {enabled}")
        await self.mqtt_client.publish(
            topic=f"game/{system}/bn{game}/enabled", payload="1" if enabled else "0", qos=1, retain=True
        )

    async def set_worker_enabled(self, worker_name: str, enabled: bool) -> None:
        logger.info(f"Setting worker {worker_name} to {enabled}")
        await self.mqtt_client.publish(
            topic=f"worker/{worker_name}/enabled", payload="1" if enabled else "0", qos=1, retain=True
        )

    async def set_bot_enabled(self, enabled: bool) -> None:
        logger.info(f"Setting bot to {enabled}")
        await self.mqtt_client.publish(topic=f"bot/enabled", payload="1" if enabled else "0", qos=1, retain=True)

    async def disconnect(self) -> None:
        self._mqtt_update_task.cancel()
        try:
            await self._mqtt_update_task
        except asyncio.CancelledError:
            pass
        await self.amqp_connection.close()
        await self.mqtt_client.disconnect()
