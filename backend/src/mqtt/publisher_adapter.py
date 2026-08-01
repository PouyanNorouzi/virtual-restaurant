"""Implements the domain layer's FoodPublisher/RejectionPublisher ports on
top of an aiomqtt client. Translates domain dataclasses -> wire schemas ->
JSON, and publishes at QoS 1 (at-least-once - losing a FOOD event silently
would violate the "food must show up" requirement).
"""

import logging

import aiomqtt

from ..domain.models import FoodReady, OrderAccepted
from ..domain.schemas import (
    FoodMessageV1,
    OccupancyMessageV1,
    OrderAcceptedMessageV1,
    RejectionMessageV1,
    SeatStatusMessageV1,
)
from .topics import (
    OCCUPANCY_TOPIC,
    food_topic,
    order_accepted_topic,
    rejected_topic,
    seating_status_topic,
)

logger = logging.getLogger(__name__)

QOS_AT_LEAST_ONCE = 1


class MqttFoodPublisher:
    def __init__(self, client: aiomqtt.Client) -> None:
        self._client = client

    async def publish_accepted(self, accepted: OrderAccepted) -> None:
        message = OrderAcceptedMessageV1(
            client_order_id=accepted.client_order_id,
            table_id=accepted.table_id,
            prep_seconds=accepted.prep_seconds,
        )
        try:
            await self._client.publish(
                order_accepted_topic(accepted.table_id),
                payload=message.model_dump_json(by_alias=True),
                qos=QOS_AT_LEAST_ONCE,
                retain=False,
            )
        except aiomqtt.MqttError:
            logger.exception(
                "failed to publish order-accepted event",
                extra={
                    "event": "order_accepted_publish_failed",
                    "client_order_id": accepted.client_order_id,
                    "table_id": accepted.table_id,
                },
            )

    async def publish_food(self, food: FoodReady) -> None:
        message = FoodMessageV1(
            order_id=food.order_id,
            client_order_id=food.client_order_id,
            food_name=food.food_name,
            table_id=food.table_id,
            ready_at=food.ready_at,
            prep_seconds=food.prep_seconds,
        )
        try:
            await self._client.publish(
                food_topic(food.table_id),
                payload=message.model_dump_json(by_alias=True),
                qos=QOS_AT_LEAST_ONCE,
                retain=False,
            )
        except aiomqtt.MqttError:
            logger.exception(
                "failed to publish food event",
                extra={
                    "event": "food_publish_failed",
                    "order_id": food.order_id,
                    "table_id": food.table_id,
                },
            )


class MqttRejectionPublisher:
    def __init__(self, client: aiomqtt.Client) -> None:
        self._client = client

    async def publish_rejected(self, table_id: int, client_order_id: str, reason: str) -> None:
        message = RejectionMessageV1(client_order_id=client_order_id, reason=reason)
        try:
            await self._client.publish(
                rejected_topic(table_id),
                payload=message.model_dump_json(by_alias=True),
                qos=QOS_AT_LEAST_ONCE,
                retain=False,
            )
        except aiomqtt.MqttError:
            logger.exception(
                "failed to publish rejection event",
                extra={"event": "rejection_publish_failed", "table_id": table_id},
            )


class MqttSeatingStatusPublisher:
    def __init__(self, client: aiomqtt.Client) -> None:
        self._client = client

    async def publish_assigned(self, session_id: str, table_id: int) -> None:
        await self._publish(session_id, SeatStatusMessageV1(state="assigned", table_id=table_id))

    async def publish_queued(self, session_id: str, queue_position: int) -> None:
        await self._publish(
            session_id, SeatStatusMessageV1(state="queued", queue_position=queue_position)
        )

    async def publish_vacated(self, session_id: str) -> None:
        await self._publish(session_id, SeatStatusMessageV1(state="vacated"))

    async def publish_warning(self, session_id: str, grace_seconds: float) -> None:
        await self._publish(
            session_id, SeatStatusMessageV1(state="warning", grace_seconds=grace_seconds)
        )

    async def _publish(self, session_id: str, message: SeatStatusMessageV1) -> None:
        try:
            await self._client.publish(
                seating_status_topic(session_id),
                # exclude_none: table_id/queue_position are only meaningful
                # for "assigned"/"queued" respectively - without this, every
                # status message would carry both as null, which doesn't
                # match the payload contract documented in README.md.
                payload=message.model_dump_json(by_alias=True, exclude_none=True),
                qos=QOS_AT_LEAST_ONCE,
                # Not retained: request_seat() is idempotent, so a client
                # that reconnects and missed its own status update can just
                # re-publish "request" to resync current state on demand.
                retain=False,
            )
        except aiomqtt.MqttError:
            logger.exception(
                "failed to publish seating status event",
                extra={"event": "seat_status_publish_failed", "session_id": session_id},
            )


class MqttOccupancyPublisher:
    def __init__(self, client: aiomqtt.Client) -> None:
        self._client = client

    async def publish_occupancy(
        self, occupied_tables: list[int], num_tables: int, queue_length: int
    ) -> None:
        message = OccupancyMessageV1(
            occupied_tables=occupied_tables, num_tables=num_tables, queue_length=queue_length
        )
        try:
            await self._client.publish(
                OCCUPANCY_TOPIC,
                payload=message.model_dump_json(by_alias=True),
                qos=QOS_AT_LEAST_ONCE,
                # Retained (unlike every other topic in this project): a
                # client that hasn't subscribed yet has no other way to
                # learn current occupancy short of waiting for the next
                # mutation, unlike seat status which can be resynced
                # on-demand via request_seat()'s idempotency.
                retain=True,
            )
        except aiomqtt.MqttError:
            logger.exception(
                "failed to publish occupancy event", extra={"event": "occupancy_publish_failed"}
            )
