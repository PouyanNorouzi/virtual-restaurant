"""Owns the aiomqtt connection lifecycle: connect, subscribe, dispatch
inbound messages, and reconnect with exponential backoff on disconnect.
"""

import asyncio
import logging

import aiomqtt

from ..config import Settings
from ..domain.order_service import OrderService
from ..domain.seating_service import SeatingService
from .publisher_adapter import (
    MqttFoodPublisher,
    MqttOccupancyPublisher,
    MqttRejectionPublisher,
    MqttSeatingStatusPublisher,
)
from .subscriber import handle_messages
from .topics import (
    ORDER_SUBSCRIBE_FILTER,
    SEATING_FINISHED_SUBSCRIBE_FILTER,
    SEATING_REQUEST_SUBSCRIBE_FILTER,
    SEATING_VACATE_SUBSCRIBE_FILTER,
)

logger = logging.getLogger(__name__)


async def run_forever(settings: Settings, stop_event: asyncio.Event) -> None:
    """Connects to the broker and processes messages until stop_event is set,
    reconnecting with exponential backoff on any MQTT-level error.
    """
    backoff = settings.reconnect_initial_backoff_seconds

    while not stop_event.is_set():
        try:
            logger.info(
                "connecting to broker",
                extra={
                    "event": "broker_connect",
                    "host": settings.broker_host,
                    "port": settings.broker_port,
                },
            )
            async with aiomqtt.Client(
                hostname=settings.broker_host,
                port=settings.broker_port,
                username=settings.broker_username,
                password=settings.broker_password,
            ) as client:
                logger.info("connected to broker", extra={"event": "broker_connect"})
                backoff = settings.reconnect_initial_backoff_seconds

                await client.subscribe(ORDER_SUBSCRIBE_FILTER, qos=1)
                await client.subscribe(SEATING_REQUEST_SUBSCRIBE_FILTER, qos=1)
                await client.subscribe(SEATING_VACATE_SUBSCRIBE_FILTER, qos=1)
                await client.subscribe(SEATING_FINISHED_SUBSCRIBE_FILTER, qos=1)

                seating_service = SeatingService(
                    num_tables=settings.num_tables,
                    status_publisher=MqttSeatingStatusPublisher(client),
                    occupancy_publisher=MqttOccupancyPublisher(client),
                    max_dining_seconds=settings.max_dining_seconds,
                    eviction_warning_grace_seconds=settings.eviction_warning_grace_seconds,
                    dawdle_check_interval_seconds=settings.dawdle_check_interval_seconds,
                )
                seating_service.start()
                order_service = OrderService(
                    num_tables=settings.num_tables,
                    min_delay_seconds=settings.min_delay_seconds,
                    max_delay_seconds=settings.max_delay_seconds,
                    max_food_name_len=settings.max_food_name_len,
                    max_pending_per_table=settings.max_pending_per_table,
                    publisher=MqttFoodPublisher(client),
                    rejection_publisher=MqttRejectionPublisher(client),
                    seating_query=seating_service,
                )

                dispatch_task = asyncio.create_task(
                    handle_messages(client, order_service, seating_service)
                )
                stop_task = asyncio.create_task(stop_event.wait())
                try:
                    await asyncio.wait(
                        {dispatch_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    dispatch_task.cancel()
                    stop_task.cancel()
                    await order_service.shutdown()
                    await seating_service.shutdown()

        except aiomqtt.MqttError as exc:
            if stop_event.is_set():
                break
            logger.warning(
                "broker disconnected, retrying",
                extra={
                    "event": "broker_disconnect",
                    "reason": str(exc),
                    "backoff_seconds": backoff,
                },
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, settings.reconnect_max_backoff_seconds)
