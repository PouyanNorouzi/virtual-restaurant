"""Parses inbound ORDER/seat-request/vacate messages and forwards them to
the appropriate domain service.

This is the only place raw JSON/MQTT concepts meet the domain layer: it
turns a topic + payload bytes into a validated domain call (or logs and
drops the message), so OrderService/SeatingService never have to know
about MQTT, JSON, or malformed input.

aiomqtt.Client.messages is a single-consumer async iterator, so order and
seating messages MUST be dispatched from one shared loop here rather than
two independent `async for message in client.messages` consumers (which
would nondeterministically split messages between them).
"""

import logging
from typing import TypeVar

import aiomqtt
from pydantic import BaseModel, ValidationError

from ..domain.models import OrderRequest
from ..domain.order_service import OrderService
from ..domain.schemas import (
    OrderMessageV1,
    SeatRequestMessageV1,
    SeatVacateMessageV1,
    utcnow,
)
from ..domain.seating_service import SeatingService
from .topics import (
    parse_session_id_from_seating_request_topic,
    parse_session_id_from_seating_vacate_topic,
    parse_table_id_from_order_topic,
)

logger = logging.getLogger(__name__)


async def handle_messages(
    client: aiomqtt.Client, order_service: OrderService, seating_service: SeatingService
) -> None:
    async for message in client.messages:
        await _handle_one(message, order_service, seating_service)


async def _handle_one(
    message: aiomqtt.Message, order_service: OrderService, seating_service: SeatingService
) -> None:
    topic = message.topic.value
    raw = message.payload
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")

    table_id = parse_table_id_from_order_topic(topic)
    if table_id is not None:
        await _handle_order(topic, raw, table_id, order_service)
        return

    session_id = parse_session_id_from_seating_request_topic(topic)
    if session_id is not None:
        if _validate_payload(topic, raw, SeatRequestMessageV1) is None:
            return
        await seating_service.request_seat(session_id)
        return

    session_id = parse_session_id_from_seating_vacate_topic(topic)
    if session_id is not None:
        if _validate_payload(topic, raw, SeatVacateMessageV1) is None:
            return
        await seating_service.vacate(session_id)
        return

    logger.warning(
        "message on unrecognized topic shape",
        extra={"event": "malformed_payload", "topic": topic},
    )


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _validate_payload(topic: str, raw: str, model: type[_ModelT]) -> _ModelT | None:
    try:
        return model.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        logger.warning(
            "malformed payload",
            extra={
                "event": "malformed_payload",
                "topic": topic,
                "byte_length": len(raw) if raw else 0,
                "error": str(exc),
            },
        )
        return None


async def _handle_order(topic: str, raw: str, table_id: int, order_service: OrderService) -> None:
    parsed = _validate_payload(topic, raw, OrderMessageV1)
    if parsed is None:
        return

    request = OrderRequest(
        table_id=table_id,
        food_name=parsed.food_name,
        client_order_id=parsed.client_order_id,
        session_id=parsed.session_id,
        submitted_at=parsed.submitted_at or utcnow(),
    )
    await order_service.handle_order(request)
